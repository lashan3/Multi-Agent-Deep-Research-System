/* Deep Research Agent — Web UI front-end.

   Streams research progress over Server-Sent Events, splits the stream into
   "thinking" (inside <think>...</think>) and "report" sections, renders the
   report as markdown with Mermaid charts, syntax-highlighted code, and
   sandboxed HTML chart artifacts.
*/

(() => {
  const el = (id) => document.getElementById(id);

  // ── Theme toggle ────────────────────────────────────────────────
  const themeBtn = el("themeBtn");
  const themeIcon = el("themeIcon");
  const SUN_ICON = '<circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>';
  const MOON_ICON = '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>';

  const setTheme = (theme) => {
    document.body.dataset.theme = theme;
    localStorage.setItem("theme", theme);
    themeIcon.innerHTML = theme === "dark" ? MOON_ICON : SUN_ICON;
    if (window.__mermaid) {
      window.__mermaid.initialize({
        startOnLoad: false,
        theme: theme === "dark" ? "dark" : "default",
        securityLevel: "loose",
        fontFamily: "Inter, system-ui, sans-serif",
      });
    }
  };
  setTheme(localStorage.getItem("theme") || "dark");
  themeBtn.addEventListener("click", () => {
    setTheme(document.body.dataset.theme === "dark" ? "light" : "dark");
  });

  // ── Settings drawer ─────────────────────────────────────────────
  const drawer = el("settingsPanel");
  const scrim = el("drawerScrim");
  const openDrawer = () => {
    drawer.classList.add("open");
    scrim.classList.add("open");
    drawer.setAttribute("aria-hidden", "false");
  };
  const closeDrawer = () => {
    drawer.classList.remove("open");
    scrim.classList.remove("open");
    drawer.setAttribute("aria-hidden", "true");
  };
  el("settingsBtn").addEventListener("click", openDrawer);
  el("settingsClose").addEventListener("click", closeDrawer);
  scrim.addEventListener("click", closeDrawer);

  // ── Example chips ───────────────────────────────────────────────
  document.querySelectorAll(".example-chip").forEach((btn) => {
    btn.addEventListener("click", () => {
      el("queryInput").value = btn.dataset.example;
      el("queryInput").focus();
    });
  });

  // ── Markdown renderer config ────────────────────────────────────
  marked.setOptions({
    breaks: false,
    gfm: true,
    smartypants: false,
  });

  /** Render a blob of markdown into an HTML string, with code highlighted. */
  function renderMarkdown(md) {
    const renderer = new marked.Renderer();
    const origCode = renderer.code.bind(renderer);
    renderer.code = (code, lang) => {
      // Mermaid: defer rendering to mermaid.run()
      if (lang === "mermaid") {
        const id = `mm-${Math.random().toString(36).slice(2, 9)}`;
        return `<div class="mermaid-block"><div class="mermaid" id="${id}">${escapeHtml(
          code,
        )}</div></div>`;
      }
      // ECharts / Plotly etc. ship as full HTML — sandbox in iframe
      if (lang === "html") {
        const safe = code.replace(/<\/script>/gi, "<\\/script>");
        return `<div class="html-chart"><iframe sandbox="allow-scripts" srcdoc="${escapeAttr(
          safe,
        )}"></iframe></div>`;
      }
      // Standard code block — try highlight.js
      try {
        if (window.hljs && lang && hljs.getLanguage(lang)) {
          const highlighted = hljs.highlight(code, { language: lang }).value;
          return `<pre><code class="hljs language-${lang}">${highlighted}</code></pre>`;
        }
      } catch (e) {}
      return origCode(code, lang);
    };

    const dirty = marked.parse(md, { renderer });
    return DOMPurify.sanitize(dirty, {
      ADD_TAGS: ["iframe"],
      ADD_ATTR: ["sandbox", "srcdoc", "class"],
    });
  }

  function escapeHtml(s) {
    return s
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }
  function escapeAttr(s) {
    return s
      .replace(/&/g, "&amp;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // ── Streaming state ─────────────────────────────────────────────
  const form = el("queryForm");
  const input = el("queryInput");
  const submitBtn = el("submitBtn");
  const statusBar = el("statusBar");
  const output = el("output");
  const main = document.querySelector(".main");

  let inThink = false;
  let thinkBuffer = "";
  let reportBuffer = "";
  let thinkEl = null;
  let thinkBodyEl = null;
  let reportEl = null;
  let abortController = null;

  function newThinkBlock() {
    thinkEl = document.createElement("div");
    thinkEl.className = "think open";
    thinkEl.innerHTML = `
      <div class="think__header">
        <div class="think__title">Thinking</div>
        <svg class="think__chevron" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
      </div>
      <div class="think__body"></div>`;
    thinkBodyEl = thinkEl.querySelector(".think__body");
    thinkEl.querySelector(".think__header").addEventListener("click", () => {
      thinkEl.classList.toggle("open");
    });
    output.appendChild(thinkEl);
  }

  function newReportBlock() {
    reportEl = document.createElement("div");
    reportEl.className = "report";
    output.appendChild(reportEl);
  }

  /** Apply incoming stream chunks, splitting at <think>...</think> boundaries. */
  function processChunk(chunk) {
    let remaining = chunk;
    while (remaining.length > 0) {
      if (!inThink) {
        const idx = remaining.indexOf("<think>");
        if (idx === -1) {
          reportBuffer += remaining;
          remaining = "";
        } else {
          if (idx > 0) reportBuffer += remaining.slice(0, idx);
          remaining = remaining.slice(idx + 7);
          inThink = true;
          if (!thinkEl) newThinkBlock();
        }
      } else {
        const idx = remaining.indexOf("</think>");
        if (idx === -1) {
          thinkBuffer += remaining;
          remaining = "";
        } else {
          thinkBuffer += remaining.slice(0, idx);
          remaining = remaining.slice(idx + 8);
          inThink = false;
          if (thinkEl) thinkEl.classList.add("done");
        }
      }
    }
    renderBuffers();
  }

  function renderBuffers() {
    if (thinkBodyEl) {
      thinkBodyEl.innerHTML = renderMarkdown(thinkBuffer || "");
    }
    if (!reportEl && reportBuffer.trim().length > 0) {
      newReportBlock();
    }
    if (reportEl) {
      reportEl.innerHTML = renderMarkdown(reportBuffer || "");
      runMermaid(reportEl);
    }
  }

  let mermaidRunQueued = false;
  function runMermaid(scope) {
    if (!window.__mermaid) return;
    if (mermaidRunQueued) return;
    mermaidRunQueued = true;
    requestAnimationFrame(async () => {
      mermaidRunQueued = false;
      const nodes = scope.querySelectorAll(".mermaid:not([data-rendered])");
      for (const n of nodes) {
        const id = n.id || `mm-${Math.random().toString(36).slice(2, 9)}`;
        n.id = id;
        n.dataset.rendered = "true";
        const src = n.textContent;
        try {
          const { svg } = await window.__mermaid.render(`${id}-svg`, src);
          n.innerHTML = svg;
        } catch (err) {
          n.innerHTML = `<pre style="color:var(--danger)">Mermaid error: ${escapeHtml(
            err.message || String(err),
          )}</pre>`;
        }
      }
    });
  }

  function resetState() {
    inThink = false;
    thinkBuffer = "";
    reportBuffer = "";
    thinkEl = null;
    thinkBodyEl = null;
    reportEl = null;
    output.innerHTML = "";
  }

  // ── Submit handler ──────────────────────────────────────────────
  function getOverrides() {
    const intOrNull = (id) => {
      const v = el(id).value.trim();
      return v ? parseInt(v, 10) : null;
    };
    const strOrNull = (id) => {
      const v = el(id).value.trim();
      return v || null;
    };
    return {
      brain_model: strOrNull("brainModel"),
      fast_model: strOrNull("fastModel"),
      max_react_steps: intOrNull("maxSteps"),
      max_reads: intOrNull("maxReads"),
      max_charts: intOrNull("maxCharts"),
      allow_clarification: el("allowClarify").checked,
    };
  }

  async function runResearch(query) {
    abortController = new AbortController();
    submitBtn.disabled = true;
    statusBar.classList.remove("is-error");
    statusBar.textContent = "Connecting…";
    main.classList.add("main--running");
    resetState();

    const overrides = getOverrides();
    const body = {
      query,
      ...Object.fromEntries(Object.entries(overrides).filter(([_, v]) => v !== null)),
    };

    try {
      const resp = await fetch("/api/research", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: abortController.signal,
      });

      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.error || `HTTP ${resp.status}`);
      }

      statusBar.textContent = "Researching…";

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });

        // Parse SSE events: each event ends with "\n\n"
        let idx;
        while ((idx = buf.indexOf("\n\n")) !== -1) {
          const rawEvent = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          for (const line of rawEvent.split("\n")) {
            if (!line.startsWith("data: ")) continue;
            try {
              const event = JSON.parse(line.slice(6));
              if (event.type === "chunk") {
                processChunk(event.content);
              } else if (event.type === "error") {
                statusBar.textContent = `Error: ${event.content}`;
                statusBar.classList.add("is-error");
              } else if (event.type === "done") {
                statusBar.textContent = "Done.";
              }
            } catch {}
          }
        }
      }
    } catch (err) {
      if (err.name === "AbortError") {
        statusBar.textContent = "Cancelled.";
      } else {
        statusBar.textContent = `Error: ${err.message}`;
        statusBar.classList.add("is-error");
      }
    } finally {
      submitBtn.disabled = false;
      abortController = null;
    }
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const q = input.value.trim();
    if (!q) return;
    runResearch(q);
  });

  // Submit on Enter (Shift+Enter = newline)
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      form.dispatchEvent(new Event("submit", { cancelable: true }));
    }
  });

  // Auto-resize the textarea
  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 200) + "px";
  });

  input.focus();
})();
