"""ReAct tool implementations — what happens when the brain calls each tool.

Tools:
  search_web      → Perplexity Search API → ranked summaries
  read_url        → Perplexity Agent API full-fetch → extracted findings JSON
  extract_data    → LLM-based structured-data extraction
  generate_chart  → dedicated LLM call to produce mermaid/echarts/plotly code
  finish_research → signals the brain is done

Notebook updates and verification are NOT tools — they're orchestrator code in
agent.py.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional

import requests

from deep_research.config import (
    PERPLEXITY_AGENT_URL,
    PERPLEXITY_SEARCH_URL,
    READER_MAX_OUTPUT_TOKENS,
    MAX_TOKENS_PER_PAGE,
    FAST_TEMPERATURE,
    FAST_TIMEOUT,
    SCHEMA_REPAIR_ATTEMPTS,
)
from deep_research import data_analysis
from deep_research.llm import call_llm, strip_code_fences
from deep_research.notebook import ResearchNotebook
from deep_research.prompts import CHART_GENERATION_PROMPT, EXTRACT_FINDINGS_PROMPT
from deep_research.source_policy import rank_results


# =============================================================================
# search_web
# =============================================================================


def search_web(
    perplexity_api_key: str,
    queries: List[str],
    notebook: ResearchNotebook,
    max_results: int = 10,
    date_range: Optional[str] = None,
) -> dict:
    """Execute web searches via the Perplexity Search API.

    Returns pre-ranked result summaries (title + url + evidence_type +
    freshness + snippet) — never raw page content.
    """
    if not perplexity_api_key:
        return {"error": "PERPLEXITY_API_KEY not set", "results": []}

    def _fetch_query(q: str) -> List[dict]:
        payload: dict = {
            "query": q,
            "max_results": max_results,
            "max_tokens_per_page": MAX_TOKENS_PER_PAGE,
        }
        if date_range:
            payload["date_range"] = date_range

        resp = requests.post(
            PERPLEXITY_SEARCH_URL,
            headers={
                "Authorization": f"Bearer {perplexity_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=(60, 200),
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Perplexity Search {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("results", [data])
        return []

    all_raw: List[dict] = []
    errors: List[str] = []
    with ThreadPoolExecutor(max_workers=min(len(queries), 5)) as pool:
        futures = {pool.submit(_fetch_query, q): q for q in queries}
        for future in as_completed(futures):
            try:
                all_raw.extend(future.result())
            except Exception as e:
                errors.append(str(e))

    if not all_raw:
        return {"error": "; ".join(errors) if errors else "No results", "results": []}

    seen_urls = {s.url for s in notebook.sources}
    ranked = rank_results(all_raw, seen_urls)

    summaries = [
        {
            "url": r.get("url", ""),
            "title": r.get("title", "Untitled"),
            "evidence_type": r.get("evidence_type", "unknown"),
            "freshness": r.get("freshness", "unknown"),
            "date": r.get("date", r.get("last_updated", "")),
            "snippet": (r.get("snippet") or r.get("content", ""))[:150],
        }
        for r in ranked
    ]

    return {
        "results_for_brain": summaries,
        "_raw_ranked": ranked,
        "total_found": len(all_raw),
        "new_after_dedup": len(ranked),
        "errors": errors if errors else None,
    }


# =============================================================================
# read_url
# =============================================================================


def read_url(
    perplexity_api_key: str,
    url: str,
    reason: str,
    notebook: ResearchNotebook,
    api_key: str,
    base_url: str,
    extract_model: str,
    reader_model: str,
) -> dict:
    """Fetch full page text via Perplexity Agent, then extract findings.

    The brain receives extracted findings as structured JSON — never raw text.
    Raw page text is discarded after extraction.
    """
    if not perplexity_api_key:
        return {"error": "PERPLEXITY_API_KEY not set", "fetch_success": False}

    url_index = notebook.source_url_index()
    source_id = url_index.get(url)

    full_text = ""
    fetch_success = False
    try:
        resp = requests.post(
            PERPLEXITY_AGENT_URL,
            headers={
                "Authorization": f"Bearer {perplexity_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": reader_model,
                "input": (
                    f"Fetch the URL and return ONLY the main article or page content. "
                    f"Exclude navigation, ads, cookie notices, footers, and sidebars. "
                    f"Preserve headings, bullet points, and tables. "
                    f"Include the publication date and author if visible. URL: {url}"
                ),
                "tools": [{"type": "fetch_url"}],
                "max_output_tokens": READER_MAX_OUTPUT_TOKENS,
            },
            timeout=(60, 200),
        )
        if resp.status_code == 200:
            data = resp.json()
            full_text = _extract_agent_text(data)
            fetch_success = bool(full_text and len(full_text) > 200)
    except Exception:
        pass

    source_meta = {"url": url, "fetch_success": fetch_success, "source_id": source_id}

    if not full_text:
        return {
            **source_meta,
            "findings": [],
            "tables": [],
            "error": "fetch failed or empty content",
        }

    sq_list = [{"id": sq.id, "question": sq.question} for sq in notebook.subquestions]

    extracted = _extract_findings_from_text(
        text=full_text,
        url=url,
        reason=reason,
        notebook=notebook,
        api_key=api_key,
        base_url=base_url,
        model=extract_model,
        sq_list=sq_list,
    )

    tables = []
    if notebook.understanding and notebook.understanding.data_likely:
        tables = data_analysis.extract_tables_from_text(full_text, url)

    return {
        **source_meta,
        "findings": extracted.get("findings", []),
        "tables": tables,
        "title": extracted.get("title", ""),
        "evidence_type": extracted.get("evidence_type", "unknown"),
        "fetch_success": fetch_success,
    }


def _extract_agent_text(data: dict) -> str:
    """Extract text from a Perplexity Agent API response."""
    output = data.get("output", [])
    if isinstance(output, list):
        texts = []
        for item in output:
            content = item.get("content", [])
            if isinstance(content, list):
                for c in content:
                    if c.get("type") == "output_text" and c.get("text"):
                        texts.append(c["text"])
            elif isinstance(content, str):
                texts.append(content)
        if texts:
            return "\n".join(texts)
    if data.get("output_text"):
        return data["output_text"]
    return ""


def _extract_findings_from_text(
    text: str,
    url: str,
    reason: str,
    notebook: ResearchNotebook,
    api_key: str,
    base_url: str,
    model: str,
    sq_list: list,
) -> dict:
    """LLM-based finding extraction from full page text."""
    query = notebook.query
    query_type = notebook.understanding.query_type if notebook.understanding else "open-ended"

    prompt = EXTRACT_FINDINGS_PROMPT.format(
        query=query,
        query_type=query_type,
        reason=reason,
        sq_list=json.dumps(sq_list, indent=2),
    )

    def _call(extra: str = "") -> str:
        response = call_llm(
            api_key=api_key,
            base_url=base_url,
            model=model,
            messages=[
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": f"URL: {url}\n\nContent:\n{text}"
                    + (f"\n\n{extra}" if extra else ""),
                },
            ],
            temperature=FAST_TEMPERATURE,
            timeout=FAST_TIMEOUT,
            max_tokens=12000,
        )
        return response["choices"][0]["message"].get("content", "{}")

    raw = strip_code_fences(_call())
    for attempt in range(SCHEMA_REPAIR_ATTEMPTS + 1):
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            if attempt < SCHEMA_REPAIR_ATTEMPTS:
                raw = strip_code_fences(
                    _call(f"Invalid JSON. Error: {e}. Return ONLY valid JSON.")
                )

    return {"findings": [], "evidence_type": "unknown", "title": ""}


# =============================================================================
# extract_data
# =============================================================================


def extract_data(
    source_id: str,
    data_type: str,
    purpose: str,
    notebook: ResearchNotebook,
    api_key: str,
    base_url: str,
    model: str,
) -> dict:
    """Extract a structured table/timeline/numbers/entities from a registered source.

    Uses the source's already-extracted findings as a text proxy (raw page text
    was discarded after the read_url call).
    """
    source = notebook.get_source(source_id)
    if not source:
        return {"error": f"Source {source_id} not found in notebook"}

    source_findings = [f for f in notebook.findings if f.source_id == source_id]
    if not source_findings:
        return {"error": f"No findings for source {source_id} to extract data from"}

    findings_text = "\n".join(f"- {f.text}" for f in source_findings)
    table = data_analysis.extract_structured_data(
        text=findings_text,
        source_id=source_id,
        data_type=data_type,
        purpose=purpose,
        query=notebook.query,
        api_key=api_key,
        base_url=base_url,
        model=model,
    )

    if table:
        return {
            "table_id": table.id,
            "data_type": table.data_type,
            "purpose": table.purpose,
            "markdown": table.markdown,
            "rows": len(table.rows),
            "_table_object": table,
        }

    return {"error": "Could not extract structured data", "table_id": None}


# =============================================================================
# generate_chart
# =============================================================================


def generate_chart(
    title: str,
    chart_type: str,
    data: str,
    design_notes: str,
    api_key: str,
    base_url: str,
    model: str,
) -> dict:
    """Generate chart code via a dedicated LLM call. Returns {tier, code, title, error}."""
    prompt = CHART_GENERATION_PROMPT.format(
        title=title,
        chart_type=chart_type,
        data=data,
        design_notes=design_notes
        or "Use professional defaults with clean colors and readable labels.",
    )

    def _call(extra: str = "") -> str:
        response = call_llm(
            api_key=api_key,
            base_url=base_url,
            model=model,
            messages=[
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": "Generate the chart visualization. Return valid JSON only."
                    + (f"\n\n{extra}" if extra else ""),
                },
            ],
            temperature=0.2,
            timeout=FAST_TIMEOUT,
            max_tokens=12000,
        )
        return response["choices"][0]["message"].get("content", "{}")

    valid_tiers = {"mermaid", "echarts", "plotly"}
    max_retries = 3

    for attempt in range(max_retries):
        raw = strip_code_fences(
            _call(
                ""
                if attempt == 0
                else f"Previous attempt returned invalid tier or empty code. Use ONLY tiers: mermaid, echarts, plotly."
            )
        )

        parsed = None
        for repair in range(SCHEMA_REPAIR_ATTEMPTS + 1):
            try:
                parsed = json.loads(raw)
                break
            except json.JSONDecodeError as e:
                if repair < SCHEMA_REPAIR_ATTEMPTS:
                    raw = strip_code_fences(
                        _call(f"Invalid JSON. Error: {e}. Return ONLY valid JSON.")
                    )

        if parsed is None:
            continue

        tier = parsed.get("tier", "mermaid")
        code = parsed.get("code", "")
        if not code or tier not in valid_tiers:
            continue

        return {"tier": tier, "code": code, "title": title, "error": None}

    return {
        "error": f"Chart generation failed after {max_retries} attempts",
        "tier": "mermaid",
        "code": "",
        "title": title,
    }
