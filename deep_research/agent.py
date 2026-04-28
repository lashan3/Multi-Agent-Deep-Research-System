"""DeepResearchAgent — the main orchestrator.

A clean, library-friendly wrapper around the 4-phase research pipeline:

    Phase 0: understand the query   (1 LLM call)
    Phase 1: build a research plan  (1 LLM call)
    Phase 2: ReAct loop             (N brain turns)
    Phase 3: streaming report       (outline + writer with chart injection)

Usage:

    from deep_research import DeepResearchAgent

    agent = DeepResearchAgent()                       # picks up .env
    for chunk in agent.research("Your question..."):
        print(chunk, end="", flush=True)
"""

from __future__ import annotations

import json
import re
from typing import Generator, List, Optional, Union

from deep_research import react_tools, report_writer
from deep_research.config import Config, ResolvedConfig
from deep_research.llm import call_llm_with_retry, strip_code_fences, window_brain_messages
from deep_research.notebook import (
    BudgetManager,
    ChartArtifact,
    Finding,
    ResearchNotebook,
    Source,
    SubQuestion,
)
from deep_research.prompts import (
    BRAIN_CONTEXT_INJECTION,
    BRAIN_SYSTEM_PROMPT,
    RESEARCH_PLAN_PROMPT,
)
from deep_research.query_understanding import understand_query
from deep_research.source_policy import enrich_source
from deep_research.tool_schemas import ALL_REACT_TOOLS


CLARIFICATION_MARKER = "Before I begin researching"


class DeepResearchAgent:
    """Multi-agent ReAct research with cited markdown reports."""

    def __init__(self, config: Optional[Union[Config, ResolvedConfig]] = None) -> None:
        if config is None:
            config = Config()
        if isinstance(config, Config):
            config = config.resolved()
        self.config: ResolvedConfig = config

    # ────────────────────────────────────────────────────────────────
    # Public API
    # ────────────────────────────────────────────────────────────────

    def research(
        self,
        query: str,
        *,
        allow_clarification: bool = True,
        conversation: Optional[List[dict]] = None,
    ) -> Generator[str, None, None]:
        """Run the full research pipeline and stream the markdown report.

        Args:
            query: the research question.
            allow_clarification: if True (default) and the understanding stage
                decides clarification would help, yield the questions and stop.
                Pass False to always proceed straight to research.
            conversation: optional prior conversation history (the messages
                sent to the understanding LLM in a previous run, e.g. when
                the user is answering clarifying questions). Internal-ish —
                most callers won't need this.
        """
        try:
            yield from self._run(query, allow_clarification, conversation)
        except Exception as e:
            yield f"\n\n**Pipeline error:** {e}\n"

    # ────────────────────────────────────────────────────────────────
    # Internal — full pipeline
    # ────────────────────────────────────────────────────────────────

    def _run(
        self,
        query: str,
        allow_clarification: bool,
        conversation: Optional[List[dict]],
    ) -> Generator[str, None, None]:
        c = self.config

        # ── Phase 0: Understanding ──────────────────────────────────
        try:
            understanding = understand_query(
                query=query,
                api_key=c.openai_api_key,
                base_url=c.openai_base_url,
                model=c.brain_model,
                timeout=c.brain_timeout,
                conversation_history=conversation,
            )
        except Exception:
            from deep_research.notebook import QueryUnderstanding

            understanding = QueryUnderstanding(initial_subquestions=[query])

        # Optional clarification gate
        if (
            allow_clarification
            and understanding.needs_clarification
            and understanding.clarifying_questions
        ):
            yield f"**{CLARIFICATION_MARKER}**, I have a few quick questions:\n\n"
            for i, q in enumerate(understanding.clarifying_questions, 1):
                yield f"{i}. {q}\n"
            yield (
                "\n_Reply with your answers (or rephrase your query with the "
                "details included) to proceed._\n"
            )
            return

        # Use resolved query as canonical from here on
        query = understanding.resolved_query or query

        yield "**Conducting deep research.** This may take a few minutes...\n\n"
        yield "<think>\n"

        label = query[:80] + ("..." if len(query) > 80 else "")
        yield f"Researching: {label}\n\n"
        yield (
            f"Query type: {understanding.query_type} · "
            f"Output: {understanding.output_format} · "
            f"Time: {understanding.time_sensitivity} · "
            f"Data likely: {understanding.data_likely}\n\n"
        )

        notebook = ResearchNotebook(query=query, understanding=understanding)
        budget = BudgetManager(
            max_react_steps=c.max_react_steps,
            max_reads_total=c.max_reads,
            max_charts=c.max_charts,
        )

        for sq_text in understanding.initial_subquestions:
            if sq_text:
                notebook.subquestions.append(SubQuestion(question=sq_text))

        # ── Phase 1: Plan ───────────────────────────────────────────
        yield "Building research plan...\n\n"
        plan_text, date_range = self._build_research_plan(query, understanding)
        if plan_text:
            yield f"Research plan:\n{plan_text}\n\n"

        # ── Phase 2: ReAct loop ─────────────────────────────────────
        yield "---\n\n"
        done = False
        brain_messages = [
            {
                "role": "system",
                "content": BRAIN_SYSTEM_PROMPT.format(
                    query=query,
                    query_type=understanding.query_type,
                    output_format=understanding.output_format,
                    time_sensitivity=understanding.time_sensitivity,
                    data_likely=understanding.data_likely,
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Research this query comprehensively:\n\n{query}\n\n"
                    f"Research plan:\n{plan_text or 'Begin by searching for the main aspects.'}"
                ),
            },
        ]

        step = 0
        while not budget.exhausted() and not done:
            step += 1
            budget.record_step()

            if step > 1:
                snapshot = json.dumps(notebook.brain_context_window(), indent=2)
                brain_messages.append({
                    "role": "user",
                    "content": BRAIN_CONTEXT_INJECTION.format(
                        notebook_snapshot=snapshot,
                        budget_status=budget.status_line(),
                    ),
                })

            windowed = window_brain_messages(brain_messages)

            # Proactive context guard (~4 chars per token; cap ~75% of 200K).
            estimated_tokens = sum(len(json.dumps(m)) for m in windowed) // 4
            if estimated_tokens > 150_000:
                yield "Context window nearly full — finishing with current findings.\n"
                notebook.stop_reason = "context_limit_approaching"
                break

            try:
                response = call_llm_with_retry(
                    api_key=c.openai_api_key,
                    base_url=c.openai_base_url,
                    model=c.brain_model,
                    messages=windowed,
                    tools=ALL_REACT_TOOLS,
                    temperature=c.brain_temperature,
                    timeout=c.brain_timeout,
                    max_retries=2,
                )
            except Exception as e:
                yield f"\n**Brain error (step {step}):** {e}\n"
                notebook.stop_reason = f"brain_error_step_{step}: {e}"
                break

            message = response["choices"][0]["message"]
            tool_calls = message.get("tool_calls") or []
            brain_messages.append(message)

            brain_text = message.get("content", "")
            if brain_text and brain_text.strip():
                yield f"{brain_text}\n\n"
                self._detect_new_subquestions(brain_text, notebook)

            if not tool_calls:
                done = True
                break

            # Process every tool call; never break mid-batch — orphan tool_use
            # IDs without matching tool results cause API errors.
            for tool_call in tool_calls:
                fn_name = tool_call["function"]["name"]
                tool_id = tool_call["id"]

                try:
                    fn_args = json.loads(tool_call["function"]["arguments"])
                except json.JSONDecodeError as e:
                    brain_messages.append({
                        "role": "tool",
                        "tool_call_id": tool_id,
                        "content": f"Error: malformed tool arguments — {e}.",
                    })
                    continue

                if fn_name == "search_web":
                    tool_result = yield from self._handle_search_web(
                        fn_args, notebook, budget, date_range
                    )
                elif fn_name == "read_url":
                    tool_result = yield from self._handle_read_url(fn_args, notebook, budget)
                elif fn_name == "extract_data":
                    tool_result = yield from self._handle_extract_data(fn_args, notebook)
                elif fn_name == "generate_chart":
                    tool_result = yield from self._handle_generate_chart(
                        fn_args, notebook, budget
                    )
                elif fn_name == "finish_research":
                    summary = fn_args.get("summary", "")
                    gaps = fn_args.get("gaps", []) or []
                    notebook.stop_reason = f"brain: {summary}"
                    notebook.gaps = gaps
                    yield f"**Research complete** — {summary}\n"
                    if gaps:
                        yield f"_Known gaps: {', '.join(gaps)}_\n"
                    yield "\n"
                    done = True
                    tool_result = "Acknowledged. Proceeding to report generation."
                else:
                    tool_result = f"Unknown tool: {fn_name}"

                brain_messages.append({
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "content": tool_result,
                })

            warnings = notebook.run_deterministic_checks()
            for w in warnings[:3]:
                yield f"_⚠ {w}_\n"

        if budget.exhausted() and not done:
            notebook.stop_reason = "budget exhausted"
            yield f"\n_Budget exhausted ({budget.status_line()}) — proceeding to report._\n\n"

        if not notebook.findings and not notebook.sources:
            yield "\n</think>\n\n"
            yield "**No research gathered.** Check your API keys and try again.\n"
            return

        yield (
            f"\n{budget.status_line()} · {len(notebook.findings)} findings · "
            f"{len(notebook.sources)} sources\n\n"
        )
        yield "</think>\n\n"

        if notebook.stop_reason and "error" in notebook.stop_reason:
            yield "_Note: Research encountered an error and may be incomplete._\n\n"

        # ── Phase 3: Report ─────────────────────────────────────────
        yield "**Writing report...**\n\n"
        try:
            yield from report_writer.write_report(
                notebook=notebook,
                api_key=c.openai_api_key,
                base_url=c.openai_base_url,
                brain_model=c.brain_model,
                brain_timeout=c.brain_timeout,
            )
        except Exception as e:
            yield f"\n**Report generation error:** {e}\n"

    # ────────────────────────────────────────────────────────────────
    # Phase 1 — research plan
    # ────────────────────────────────────────────────────────────────

    def _build_research_plan(self, query: str, understanding) -> tuple[str, Optional[str]]:
        """Single LLM call. Returns (plan_text, optional_date_range_iso)."""
        c = self.config
        try:
            response = call_llm_with_retry(
                api_key=c.openai_api_key,
                base_url=c.openai_base_url,
                model=c.brain_model,
                messages=[
                    {
                        "role": "system",
                        "content": RESEARCH_PLAN_PROMPT.format(
                            query=query,
                            query_type=understanding.query_type,
                            output_format=understanding.output_format,
                            time_sensitivity=understanding.time_sensitivity,
                            domain_hints=", ".join(understanding.domain_hints) or "none",
                            data_likely=understanding.data_likely,
                            hidden_subproblems="\n".join(
                                f"- {p}" for p in understanding.hidden_subproblems
                            ) or "none identified",
                        ),
                    },
                    {"role": "user", "content": f"Create research plan for: {query}"},
                ],
                temperature=0.2,
                timeout=c.brain_timeout,
                max_tokens=6000,
                max_retries=2,
            )
            raw = strip_code_fences(response["choices"][0]["message"].get("content", ""))
            plan_json = json.loads(raw)
            return plan_json.get("plan", ""), plan_json.get("date_range")
        except Exception:
            return "", None

    # ────────────────────────────────────────────────────────────────
    # Tool handlers — orchestrator-side mutations + streaming progress
    # ────────────────────────────────────────────────────────────────

    def _handle_search_web(
        self, fn_args: dict, notebook: ResearchNotebook, budget: BudgetManager, date_range
    ) -> Generator:
        c = self.config
        queries = fn_args.get("queries", [])
        reasoning = fn_args.get("reasoning", "")
        override_date_range = fn_args.get("date_range", date_range)

        yield "### Searching\n"
        for q in queries:
            yield f"- _{q}_\n"
            if q not in notebook.search_history:
                notebook.search_history.append(q)
            budget.record_search()
        if reasoning:
            yield f"\n_{reasoning}_\n"
        yield "\n"

        try:
            result = react_tools.search_web(
                perplexity_api_key=c.perplexity_api_key,
                queries=queries,
                notebook=notebook,
                max_results=c.max_search_results,
                date_range=override_date_range,
            )
        except Exception as e:
            yield f"_Search error: {e}_\n\n"
            return f"Search failed: {e}"

        if result.get("error") and not result.get("results_for_brain"):
            yield f"_Search error: {result['error']}_\n\n"
            return f"Search failed: {result['error']}"

        raw_ranked = result.get("_raw_ranked", [])
        existing_urls = {s.url for s in notebook.sources}
        new_source_count = 0
        for r in raw_ranked:
            url = r.get("url", "")
            if url and url not in existing_urls:
                notebook.sources.append(enrich_source(r))
                existing_urls.add(url)
                new_source_count += 1

        summaries = result.get("results_for_brain", [])
        yield (
            f"_Found {result.get('total_found', 0)} results, "
            f"{result.get('new_after_dedup', 0)} new · "
            f"{new_source_count} sources registered_\n\n"
        )

        return json.dumps(
            {
                "new_results": summaries[:15],
                "total_found": result.get("total_found", 0),
                "new_after_dedup": result.get("new_after_dedup", 0),
            },
            indent=2,
        )

    def _handle_read_url(
        self, fn_args: dict, notebook: ResearchNotebook, budget: BudgetManager
    ) -> Generator:
        c = self.config
        url = fn_args.get("url", "")
        reason = fn_args.get("reason", "")

        if not c.reader_enabled:
            return "Reader is disabled."
        if not budget.can_read():
            return f"Read budget exhausted ({budget.reads_used}/{budget.max_reads_total})."

        known_urls = {s.url for s in notebook.sources}
        if url not in known_urls:
            return (
                "URL not found in search results. Only read URLs returned by "
                "search_web. Use search_web first."
            )

        yield f"_Reading: {url}_\n"
        budget.record_read()

        try:
            result = react_tools.read_url(
                perplexity_api_key=c.perplexity_api_key,
                url=url,
                reason=reason,
                notebook=notebook,
                api_key=c.openai_api_key,
                base_url=c.openai_base_url,
                extract_model=c.fast_model,
                reader_model=c.reader_model,
            )
        except Exception as e:
            yield f"_Read error: {e}_\n\n"
            return f"read_url failed: {e}"

        fetch_success = result.get("fetch_success", False)
        findings_raw = result.get("findings", [])

        url_index = notebook.source_url_index()
        source_id = url_index.get(url)
        if not source_id:
            new_src = Source(
                url=url,
                title=result.get("title", ""),
                evidence_type=result.get("evidence_type", "unknown"),
                fetch_success=fetch_success,
            )
            notebook.sources.append(new_src)
            source_id = new_src.id
        else:
            src = notebook.get_source(source_id)
            if src:
                src.fetch_success = fetch_success
                if result.get("title"):
                    src.title = result["title"]
                if result.get("evidence_type"):
                    src.evidence_type = result["evidence_type"]

        new_findings: List[Finding] = []
        valid_sq_ids = {sq.id for sq in notebook.subquestions}
        for f_raw in findings_raw:
            text = f_raw.get("text", "").strip()
            if not text:
                continue
            sq_ids = [sid for sid in (f_raw.get("sq_ids", []) or []) if sid in valid_sq_ids]
            finding = Finding(
                text=text,
                source_id=source_id,
                sq_ids=sq_ids,
                confidence=f_raw.get("confidence", "medium"),
            )
            notebook.findings.append(finding)
            new_findings.append(finding)

        for t in result.get("tables", []):
            notebook.extracted_tables.append(t)

        self._update_sq_statuses(notebook)

        status = "✓ full text" if fetch_success else "✗ fallback"
        yield f"_Read {status} · {len(new_findings)} findings extracted_\n\n"

        return json.dumps(
            {
                "source_id": source_id,
                "title": result.get("title", ""),
                "evidence_type": result.get("evidence_type", "unknown"),
                "fetch_success": fetch_success,
                "findings_count": len(new_findings),
                "findings": [
                    {"id": f.id, "text": f.text[:200], "confidence": f.confidence}
                    for f in new_findings[:20]
                ],
                "tables_extracted": len(result.get("tables", [])),
            },
            indent=2,
        )

    def _handle_extract_data(
        self, fn_args: dict, notebook: ResearchNotebook
    ) -> Generator:
        c = self.config
        source_id = fn_args.get("source_id", "")
        data_type = fn_args.get("data_type", "table")
        purpose = fn_args.get("purpose", "")

        yield f"_Extracting {data_type} data from {source_id}..._\n\n"

        try:
            result = react_tools.extract_data(
                source_id=source_id,
                data_type=data_type,
                purpose=purpose,
                notebook=notebook,
                api_key=c.openai_api_key,
                base_url=c.openai_base_url,
                model=c.fast_model,
            )
        except Exception as e:
            return f"extract_data failed: {e}"

        if result.get("error"):
            return f"Extraction error: {result['error']}"

        table_obj = result.pop("_table_object", None)
        if table_obj:
            notebook.extracted_tables.append(table_obj)

        return json.dumps(result, indent=2)

    def _handle_generate_chart(
        self, fn_args: dict, notebook: ResearchNotebook, budget: BudgetManager
    ) -> Generator:
        c = self.config
        title = fn_args.get("title", "Chart")
        chart_type = fn_args.get("chart_type", "bar")
        data = fn_args.get("data", "")
        design_notes = fn_args.get("design_notes", "")
        raw_source_ids = fn_args.get("source_ids", []) or []

        valid_source_ids = {s.id for s in notebook.sources}
        chart_source_ids = [sid for sid in raw_source_ids if sid in valid_source_ids]

        if not budget.can_generate_chart():
            return f"Chart budget exhausted ({budget.charts_used}/{budget.max_charts})."

        # Force Mermaid if there's already an HTML chart in the report (only one
        # HTML artifact is visible at a time in most renderers).
        html_chart_count = sum(1 for c_ in notebook.chart_artifacts if c_.tier != "mermaid")
        if html_chart_count >= 1:
            design_notes = (
                "MANDATORY: Use tier 'mermaid' for this chart — the report already "
                "has an HTML chart and can only display one. " + (design_notes or "")
            )

        yield f"_Generating chart: {title[:60]}_\n\n"
        budget.record_chart()

        try:
            result = react_tools.generate_chart(
                title=title,
                chart_type=chart_type,
                data=data,
                design_notes=design_notes,
                api_key=c.openai_api_key,
                base_url=c.openai_base_url,
                model=c.brain_model,
            )
        except Exception as e:
            return f"generate_chart failed: {e}"

        if result.get("error"):
            yield f"_Chart error: {result['error']}_\n\n"
            return f"Chart generation error: {result['error']}"

        chart = ChartArtifact(
            title=title,
            purpose=f"{chart_type}: {title}",
            tier=result.get("tier", "mermaid"),
            chart_code=result.get("code", ""),
            source_ids=chart_source_ids,
        )
        notebook.chart_artifacts.append(chart)
        yield f"_Chart generated ({chart.tier}): {title}_\n\n"

        return json.dumps(
            {
                "chart_id": chart.id,
                "chart_title": title,
                "tier": chart.tier,
                "status": (
                    f"{chart.tier} chart stored — will be embedded in report via "
                    f"{{{{CHART:{chart.id}}}}}"
                ),
            }
        )

    # ────────────────────────────────────────────────────────────────
    # Helpers
    # ────────────────────────────────────────────────────────────────

    @staticmethod
    def _detect_new_subquestions(brain_text: str, notebook: ResearchNotebook) -> None:
        """Parse 'NEW SUB-QUESTION: ...' annotations from brain text."""
        for match in re.finditer(
            r"NEW SUB-QUESTION:\s*(.+?)(?:\n|$)", brain_text, re.IGNORECASE
        ):
            sq_text = match.group(1).strip()
            if sq_text and sq_text not in {sq.question for sq in notebook.subquestions}:
                notebook.subquestions.append(SubQuestion(question=sq_text))

    @staticmethod
    def _update_sq_statuses(notebook: ResearchNotebook) -> None:
        """Promote sub-question status based on accumulated findings.

        Promotion rules (never overrides a manual "conflicting" status):
          - ≥2 findings from tier-1 sources with high confidence → "answered" / high
          - ≥1 finding from a tier-1 source                    → "partial"  / medium
          - ≥3 findings from any source                        → "partial"  / low
        """
        if not notebook.subquestions:
            return

        source_map = {s.id: s for s in notebook.sources}
        tier1 = {"official_regulatory", "company_primary", "market_news", "academic"}

        for sq in notebook.subquestions:
            if sq.status == "conflicting":
                continue

            sq_findings = [f for f in notebook.findings if sq.id in f.sq_ids]
            if not sq_findings:
                continue

            sq.source_types_seen = list(
                {
                    source_map[f.source_id].evidence_type
                    for f in sq_findings
                    if f.source_id in source_map
                }
            )

            tier1_high = [
                f for f in sq_findings
                if f.confidence == "high"
                and source_map.get(f.source_id) is not None
                and source_map[f.source_id].evidence_type in tier1
            ]
            tier1_any = [
                f for f in sq_findings
                if source_map.get(f.source_id) is not None
                and source_map[f.source_id].evidence_type in tier1
            ]

            current = sq.status
            if len(tier1_high) >= 2 and current in ("unanswered", "partial"):
                sq.status = "answered"
                sq.confidence = "high"
            elif len(tier1_any) >= 1 and current == "unanswered":
                sq.status = "partial"
                sq.confidence = "medium"
            elif len(sq_findings) >= 3 and current == "unanswered":
                sq.status = "partial"
                sq.confidence = "low"

            if not sq.key_finding:
                high_conf = [f for f in sq_findings if f.confidence == "high"]
                sq.key_finding = (high_conf[0] if high_conf else sq_findings[0]).text[:120]
