# Architecture

This document is the technical companion to the README. It describes the
internals of the agent — how the four phases hand off, what data structures
hold session state, and the design choices that keep the brain focused and
the report grounded.

---

## High-level data flow

```
┌────────┐      ┌─────────────────┐      ┌────────────────┐
│ user   │ ───► │ Phase 0          │ ───► │ Phase 1         │
│ query  │      │ Understanding    │      │ Plan            │
└────────┘      └─────────────────┘      └────────┬───────┘
                                                  │
                ┌─────────────────────────────────┘
                ▼
        ┌─────────────────┐         ┌──────────────────┐
        │  Phase 2         │ ◄────►  │  Notebook         │
        │  ReAct loop      │         │  (in-memory)      │
        └─────────┬───────┘         └──────────────────┘
                  │
                  ▼
        ┌─────────────────┐         ┌──────────────────┐
        │  Phase 3         │ ◄───── │  Synthesis brief  │
        │  Outline+Writer  │         │  (notebook export)│
        └─────────┬───────┘         └──────────────────┘
                  │
                  ▼
            streamed
            markdown
            report
```

---

## Phase 0 — Query Understanding

`deep_research/query_understanding.py`

A single LLM call converts the user's free-text query into a structured
`QueryUnderstanding` (defined in `notebook.py`):

```python
QueryUnderstanding(
    query_type=...,             # factual / comparative / analytical / trend / open-ended
    output_format=...,          # detailed_report / short_answer / table / timeline
    time_sensitivity=...,       # current_data_required / historical_ok / timeless
    domain_hints=[...],
    hidden_subproblems=[...],
    initial_subquestions=[...], # 3–6 specific, searchable questions
    data_likely=...,            # True → look for tables/charts
    needs_clarification=...,
    clarifying_questions=[...],
    suggested_title=...,
    resolved_query=...,         # canonical, self-contained question
)
```

Two outputs matter for the rest of the pipeline:

- **`initial_subquestions`** — seed the notebook's `SubQuestion` list. Findings
  are tagged to one or more SQs so the report writer can group evidence
  thematically.
- **`needs_clarification` + `clarifying_questions`** — if the model decides
  the scope is ambiguous, the agent emits the questions and stops. The user
  can re-issue with more context.

The structured fields (especially `query_type` and `time_sensitivity`) flow
into every subsequent prompt — Phase 1 plans differently for trend vs.
comparative, Phase 3 picks different report structures.

---

## Phase 1 — Research Plan

`deep_research/agent.py :: _build_research_plan`

A single brain LLM call produces a JSON plan:

```json
{
  "plan": "...actionable strategy: what to look for, what sources to prioritise, when to stop...",
  "date_range": "2024-01-01..2025-04-07"
}
```

The plan text is injected into the brain's first user message in Phase 2;
the date range (when present) is passed to `search_web` calls so Perplexity
filters results.

Adapts to query type:
- **comparative** → covers all sides being compared
- **trend** → spans the relevant time range
- **analytical** → causes, effects, evidence

---

## Phase 2 — ReAct Loop

`deep_research/agent.py :: _run` + `deep_research/react_tools.py`

The loop runs up to `MAX_REACT_STEPS` times. On each iteration:

1. Inject the current notebook context window into the brain's message history.
2. Call the brain with the full ReAct tool set (`search_web`, `read_url`,
   `extract_data`, `generate_chart`, `finish_research`).
3. Process every tool call in the brain's response (parallel-safe — never break
   mid-batch, otherwise orphan `tool_use` IDs cause API errors).
4. After all tools return, run deterministic verification (`run_deterministic_checks`).
5. Loop until the brain calls `finish_research`, the budget runs out, or the
   context window approaches its limit.

### Tools

| Tool | What it does | What goes back to the brain |
|---|---|---|
| `search_web` | Perplexity Search API, parallelised across queries, ranked by source policy | Compact summaries (title, url, evidence_type, freshness, snippet) — never raw page content |
| `read_url` | Perplexity Agent fetch + LLM-based finding extraction | Structured findings with confidence + sub-question tags. Raw page text is discarded after extraction. |
| `extract_data` | LLM-based table/timeline/numbers/entities extraction from a registered source's findings | Markdown table + structured rows |
| `generate_chart` | Dedicated visualisation specialist call | `{tier, code}` — Mermaid (default) or HTML (ECharts/Plotly, max 1 per report) |
| `finish_research` | No-op except for marking the notebook done | Acknowledgement only |

### Notebook updates are NOT tools

The brain calls tools; the orchestrator (`agent.py`) updates the notebook.
This split is critical:

- The brain stays focused on *what to research next*, not on bookkeeping.
- Notebook state (sources, findings, sub-question status) updates
  deterministically — the brain can't accidentally corrupt it via a malformed
  tool call.
- The orchestrator can run cross-cutting passes (verification, status
  promotion) that the brain has no business doing.

### Deterministic verification

After every tool batch, `run_deterministic_checks()` runs in pure Python:

- Findings reference real source IDs and SQ IDs.
- Contradiction IDs resolve.
- Sub-questions marked `answered` must have at least one tier-1 source —
  otherwise auto-downgrade to `partial` with a `weakness` note.

These warnings stream to the user as `_⚠ ..._` lines (capped at 3 per step
to avoid noise).

### Sub-question status promotion

`_update_sq_statuses()` runs after each `read_url`:

- ≥2 tier-1 high-confidence findings → `answered` / `high`
- ≥1 tier-1 finding → `partial` / `medium`
- ≥3 any-source findings → `partial` / `low`

Never auto-overrides a manual `conflicting` status, never demotes (demotion
is the verification layer's job).

### Budget management

`BudgetManager` tracks `steps_used`, `reads_used`, `charts_used`,
`searches_used`. The brain can see the status line in its context window
injection, so it knows when to wrap up. Hard caps are enforced at the
orchestrator layer — the brain literally can't read past `MAX_READS` because
`can_read()` returns False and `read_url` returns an error.

---

## Phase 3 — Outline + Writer

`deep_research/report_writer.py`

### 3a — Outline Builder

A non-streaming brain call. Input is a summary of the synthesis brief
(theme names, source counts, gap counts). Output is a JSON outline:

```json
{
  "title": "...",
  "sections": [
    {"heading": "...", "type": "executive_summary", "content_from": [...], ...}
  ]
}
```

Section types adapt to query type — comparative leads with a comparison
table, trend leads with a timeline, etc.

### 3b — Section Writer

A *streaming* brain call. Input is the full synthesis brief. The writer
emits markdown with two special tokens:

- `[N]` — citation reference, must match a source number in the brief
- `{{CHART:cht_xxx}}` — placeholder for a stored chart

`_inject_charts()` wraps the raw token stream:

- Buffers at potential placeholder boundaries (handles `{{CHART:` split
  across stream chunks).
- When it sees a complete `{{CHART:cht_xxx}}`:
  - Looks up the chart in the notebook.
  - Mermaid → emits as a fenced ` ```mermaid ` block (rendered inline by the UI).
  - ECharts/Plotly → emits as a fenced ` ```html ` block (rendered as a
    sandboxed iframe artifact).
- All other tokens pass through verbatim.

The writer LLM never generates chart code or even sees what's in it — it
just places the placeholders. This means:
- Chart code can be 5–10 KB without burning the writer's context.
- Switching the chart specialist independently is trivial.

---

## Notebook design

`deep_research/notebook.py`

```python
ResearchNotebook
├── query: str
├── understanding: QueryUnderstanding | None
├── subquestions: List[SubQuestion]
├── sources:      List[Source]
├── findings:     List[Finding]
├── extracted_tables: List[ExtractedTable]
├── chart_artifacts:  List[ChartArtifact]
├── contradictions:   List[Contradiction]
├── search_history:   List[str]
└── stop_reason: str | None
```

Three view methods serve different consumers:

| Consumer | Method | What they see |
|---|---|---|
| The brain (per turn) | `brain_context_window()` | Compact JSON: SQ status, missing source types, search history, budget summary. **Never** raw findings. |
| Verification | `run_deterministic_checks()` | All warnings; mutates SQ status when downgrading. |
| Report writer (Phase 3) | `synthesis_brief()` | Pre-numbered sources, findings grouped by SQ, full tables, chart references. |

This separation means the brain's context stays small (a few KB) even when
findings/sources/tables are MBs.

---

## Source policy

`deep_research/source_policy.py`

Domain-based deterministic classification into one of:

- `official_regulatory` — government, regulators, central banks
- `company_primary` — investor relations, official press
- `market_news` — Reuters, Bloomberg, FT, etc.
- `analyst_forecast` — McKinsey, Gartner, Forrester, etc.
- `academic` — arxiv, pubmed, peer-reviewed journals
- `general_web` — everything else

`rank_results()` orders search results: non-weak first, then by evidence
type, then by snippet length descending. The brain sees results in this
order, so authoritative sources appear at the top of every search response.

`is_weak_signal()` flags Medium, Substack, LinkedIn, Reddit, etc. — these
still appear in results but rank below everything else.

---

## Streaming protocol

The agent yields a single stream of strings, with two parts:

```
**Conducting deep research…**

<think>
…all phase-2 progress, tool calls, results, brain reasoning…
</think>

# Report Title

## Executive Summary
…
{{CHART:cht_abc123}}        ← swapped to ```mermaid``` or ```html``` mid-stream
…
## Sources
1. *Title* [https://…](https://…) *(market_news; 2024)*
```

The web UI (`server/static/app.js`) splits at `<think>...</think>` and
renders the inside as a collapsible block; the rest as live markdown.

---

## Configuration philosophy

Three layers, in priority order:

1. **CLI / library arguments** (highest) — explicit overrides for one run.
2. **Environment variables** — set in `.env` or the deployment environment.
3. **Code defaults** — sensible production-ready values in `config.py`.

`Config.resolved()` merges them top-to-bottom. The fully-populated
`ResolvedConfig` is what the agent operates on internally — there's no
hidden global state.

---

## Why the brain never sees raw HTML

When `read_url` runs, the full page text from the Perplexity Agent is
processed by the **fast** model (e.g. Gemini Flash) into structured
findings before being returned. The brain only ever sees:

```json
{
  "source_id": "src_abc",
  "title": "…",
  "evidence_type": "official_regulatory",
  "fetch_success": true,
  "findings_count": 8,
  "findings": [{"id": "fnd_…", "text": "…", "confidence": "high"}, …]
}
```

This matters because:

1. **Cost.** Page text is often 30–50 KB; the brain (Sonnet) is 5–10×
   more expensive per token than Gemini Flash. Routing extraction to the
   cheap model saves 70%+ on read costs.
2. **Hallucination resistance.** The brain can't fabricate plausible-but-wrong
   quotes from sources it never read. Findings are atomic factual statements
   with a confidence rating attached at extraction time.
3. **Context economy.** Even Sonnet's 200K context fills up after ~10 raw
   pages. With extracted findings, the same context holds 50+ source
   summaries.

---

## Trade-offs and known limitations

- **Single reasoning owner.** One brain runs the loop end-to-end — no
  multi-brain debate. Simpler to reason about, but no diversity-of-thought.
- **No persistent memory.** Every query starts a fresh notebook. Multi-turn
  conversational research is out of scope (the design philosophy is "fire and
  forget"; for follow-ups, issue a new query that includes context).
- **Perplexity dependency.** `search_web` and `read_url` both rely on
  Perplexity's APIs. Pluggable backends are on the roadmap.
- **Chart visibility constraint.** Most renderers display only one HTML
  artifact per message; the chart specialist is hard-instructed to use Mermaid
  unless the chart type literally requires HTML (radar/heatmap/scatter).
