"""Phase 3 — Report Writer.

Two stages:

  3a. Outline Builder (1 non-streaming LLM call)
      Adapts structure to query type — comparison-first for comparative,
      timeline-first for trend, etc.

  3b. Section Writer (1 streaming LLM call)
      Writes the full report with `[N]` citations and `{{CHART:cht_xxx}}`
      placeholders. The stream wrapper swaps each placeholder for the
      stored chart code (Mermaid/ECharts/Plotly) inline as the report streams.
      The writer LLM never sees or generates chart code itself.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Generator, Optional

import requests

from deep_research.config import SCHEMA_REPAIR_ATTEMPTS
from deep_research.llm import call_llm_with_retry, stream_llm, strip_code_fences
from deep_research.notebook import ResearchNotebook
from deep_research.prompts import OUTLINE_BUILDER_PROMPT, REPORT_WRITER_SYSTEM_PROMPT


def write_report(
    notebook: ResearchNotebook,
    api_key: str,
    base_url: str,
    brain_model: str,
    brain_timeout: int,
    cancel_event: Optional[threading.Event] = None,
) -> Generator[str, None, None]:
    """Top-level: build outline, then stream the report with chart injection."""
    brief = notebook.synthesis_brief()
    if cancel_event is not None and cancel_event.is_set():
        return
    outline = _build_outline(brief, api_key, base_url, brain_model, brain_timeout)
    if cancel_event is not None and cancel_event.is_set():
        return
    yield from _write_sections(
        brief, outline, api_key, base_url, brain_model, notebook, cancel_event
    )


# =============================================================================
# 3a — Outline Builder
# =============================================================================


def _build_outline(
    brief: dict, api_key: str, base_url: str, model: str, timeout: int
) -> dict:
    query = brief.get("query", "")
    query_type = brief.get("query_type", "open-ended")
    output_format = brief.get("output_format", "detailed_report")
    themes = [t["name"] for t in brief.get("themes", [])]
    table_count = len(brief.get("tables", []))
    chart_count = len(brief.get("charts", []))
    contradiction_count = sum(1 for c in brief.get("contradictions", []) if not c["resolved"])
    sq_coverage = [
        f"{sq['question']} [{sq['status']}]" for sq in brief.get("subquestions", [])
    ]

    prompt = OUTLINE_BUILDER_PROMPT.format(
        query=query,
        query_type=query_type,
        output_format=output_format,
        sq_coverage="\n".join(sq_coverage) or "none",
        themes="\n".join(f"- {t}" for t in themes) or "none",
        table_count=table_count,
        chart_count=chart_count,
        contradiction_count=contradiction_count,
    )

    def _call(extra: str = "") -> str:
        response = call_llm_with_retry(
            api_key=api_key,
            base_url=base_url,
            model=model,
            messages=[
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": f"Build report outline for: {query}"
                    + (f"\n\n{extra}" if extra else ""),
                },
            ],
            temperature=0.1,
            timeout=timeout,
            max_tokens=12000,
            max_retries=3,
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

    return _default_outline(query_type, table_count, chart_count, contradiction_count)


def _default_outline(
    query_type: str,
    table_count: int,
    chart_count: int,
    contradiction_count: int,
) -> dict:
    """Safe fallback when the outline LLM call fails."""
    sections = [
        {"heading": "Executive Summary", "type": "executive_summary"},
        {"heading": "Key Findings", "type": "key_findings"},
    ]
    if table_count > 0:
        sections.append({"heading": "Data & Analysis", "type": "data_analysis"})
    if contradiction_count > 0:
        sections.append({"heading": "Contradictions & Caveats", "type": "contradictions"})
    sections.append({"heading": "Context & Implications", "type": "implications"})
    if query_type in ("analytical", "comparative"):
        sections.append({"heading": "Recommendations", "type": "recommendations"})
    sections.append({"heading": "Sources & References", "type": "sources"})
    return {"title": "Research Report", "sections": sections}


# =============================================================================
# 3b — Section Writer with chart injection
# =============================================================================


def _write_sections(
    brief: dict,
    outline: dict,
    api_key: str,
    base_url: str,
    model: str,
    notebook: ResearchNotebook,
    cancel_event: Optional[threading.Event] = None,
) -> Generator[str, None, None]:
    query = brief.get("query", "")
    query_type = brief.get("query_type", "open-ended")
    output_format = brief.get("output_format", "detailed_report")
    time_sensitivity = brief.get("time_sensitivity", "historical_ok")

    system_prompt = REPORT_WRITER_SYSTEM_PROMPT.format(
        query=query,
        query_type=query_type,
        output_format=output_format,
        time_sensitivity=time_sensitivity,
    )

    user_content = (
        f"Research query: {query}\n\n"
        f"Report outline:\n{json.dumps(outline, indent=2)}\n\n"
        f"---\n\nSYNTHESIS BRIEF:\n\n{json.dumps(brief, indent=2)}"
    )

    max_stream_retries = 3
    for stream_attempt in range(max_stream_retries):
        try:
            raw_stream = stream_llm(
                api_key=api_key,
                base_url=base_url,
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.3,
                max_tokens=20000,
                timeout=400,
            )
            yield from _inject_charts(raw_stream, notebook, cancel_event)
            return
        except (requests.Timeout, requests.ConnectionError) as exc:
            if stream_attempt < max_stream_retries - 1:
                wait = 2 ** stream_attempt
                yield (
                    f"\n_Writer stream interrupted ({exc.__class__.__name__}), "
                    f"retrying in {wait}s..._\n\n"
                )
                time.sleep(wait)
            else:
                yield f"\n**Report writer failed after {max_stream_retries} attempts:** {exc}\n"


# =============================================================================
# Chart placeholder injection
# =============================================================================

PLACEHOLDER_PREFIX = "{{CHART:"
PLACEHOLDER_SUFFIX = "}}"
MAX_CHART_ID_LEN = 20


def _inject_charts(
    stream: Generator[str, None, None],
    notebook: ResearchNotebook,
    cancel_event: Optional[threading.Event] = None,
) -> Generator[str, None, None]:
    """Wrap a text stream and replace `{{CHART:cht_xxx}}` with actual chart code.

    - Mermaid charts → triple-backtick `mermaid` block (rendered inline by the UI).
    - ECharts/Plotly → triple-backtick `html` block (rendered as a sandboxed
      artifact by the UI).

    Handles placeholders that span across stream chunks by buffering at
    potential placeholder boundaries. Honours `cancel_event` between chunks.
    """
    buffer = ""

    for chunk in stream:
        if cancel_event is not None and cancel_event.is_set():
            return
        buffer += chunk

        while True:
            start = buffer.find(PLACEHOLDER_PREFIX)

            if start == -1:
                # No placeholder prefix — yield everything except a small tail
                # in case a partial "{{CHART:" is forming.
                safe = len(buffer) - len(PLACEHOLDER_PREFIX) + 1
                if safe > 0:
                    yield buffer[:safe]
                    buffer = buffer[safe:]
                break

            end = buffer.find(PLACEHOLDER_SUFFIX, start + len(PLACEHOLDER_PREFIX))

            if end == -1:
                # Prefix found but no closing }} yet.
                if start > 0:
                    yield buffer[:start]
                    buffer = buffer[start:]
                # If we've buffered too much, this isn't a real placeholder.
                if len(buffer) > len(PLACEHOLDER_PREFIX) + MAX_CHART_ID_LEN + len(
                    PLACEHOLDER_SUFFIX
                ):
                    yield buffer[: len(PLACEHOLDER_PREFIX)]
                    buffer = buffer[len(PLACEHOLDER_PREFIX) :]
                    continue
                break

            # Found complete placeholder — yield text before it.
            if start > 0:
                yield buffer[:start]

            chart_id = buffer[start + len(PLACEHOLDER_PREFIX) : end].strip()

            chart = next(
                (c for c in notebook.chart_artifacts if c.id == chart_id), None
            )
            if chart and chart.chart_code:
                if chart.tier == "mermaid":
                    yield f"\n```mermaid\n{chart.chart_code}\n```\n"
                else:
                    yield f"\n```html\n{chart.chart_code}\n```\n"
            else:
                yield f"\n*[Chart {chart_id} not available]*\n"

            buffer = buffer[end + len(PLACEHOLDER_SUFFIX) :]

    if buffer:
        yield buffer
