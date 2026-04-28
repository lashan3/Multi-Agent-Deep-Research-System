"""Research notebook — the single source of truth during a research run.

The notebook lives in Python memory for the duration of one research session.
The brain never sees it raw — the orchestrator builds compact context windows
per turn via `brain_context_window()`. The report writer receives a pre-assembled
brief via `synthesis_brief()` so it never has to do ID lookups.

Key design rules:
  - Raw source text is NEVER stored — only extracted findings.
  - `brain_context_window()` returns structured JSON, not prose.
  - `synthesis_brief()` pre-numbers sources and groups findings by sub-question
    so the report writer can cite without inventing source numbers.
"""

from __future__ import annotations

import uuid
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# =============================================================================
# Query Understanding (Phase 0 output)
# =============================================================================


class QueryUnderstanding(BaseModel):
    query_type: Literal["factual", "comparative", "analytical", "trend", "open-ended"] = "open-ended"
    output_format: Literal["detailed_report", "short_answer", "table", "timeline"] = "detailed_report"
    time_sensitivity: Literal["current_data_required", "historical_ok", "timeless"] = "historical_ok"
    domain_hints: List[str] = Field(default_factory=list)
    hidden_subproblems: List[str] = Field(default_factory=list)
    initial_subquestions: List[str] = Field(default_factory=list)
    data_likely: bool = False
    needs_clarification: bool = False
    clarifying_questions: List[str] = Field(default_factory=list)
    suggested_title: str = ""
    resolved_query: str = ""


# =============================================================================
# Sub-question — one of N specific questions whose answers compose the report
# =============================================================================


class SubQuestion(BaseModel):
    id: str = Field(default_factory=lambda: f"sq_{uuid.uuid4().hex[:6]}")
    question: str
    status: Literal["unanswered", "partial", "answered", "conflicting"] = "unanswered"
    confidence: Literal["high", "medium", "low", "none"] = "none"
    key_finding: Optional[str] = None
    source_types_seen: List[str] = Field(default_factory=list)
    weakness: Optional[str] = None


# =============================================================================
# Source — a registered URL with classification metadata
# =============================================================================


class Source(BaseModel):
    id: str = Field(default_factory=lambda: f"src_{uuid.uuid4().hex[:6]}")
    url: str
    title: str
    domain: str = ""
    evidence_type: Literal[
        "official_regulatory",
        "company_primary",
        "market_news",
        "analyst_forecast",
        "academic",
        "general_web",
        "unknown",
    ] = "unknown"
    freshness: Literal["current", "recent", "outdated", "unknown"] = "unknown"
    fetch_success: bool = False


# =============================================================================
# Finding — atomic factual statement linked to a source and one or more SQs
# =============================================================================


class Finding(BaseModel):
    id: str = Field(default_factory=lambda: f"fnd_{uuid.uuid4().hex[:6]}")
    text: str
    source_id: str
    sq_ids: List[str] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "medium"


# =============================================================================
# ExtractedTable — a structured table or timeline pulled from a source
# =============================================================================


class ExtractedTable(BaseModel):
    id: str = Field(default_factory=lambda: f"tbl_{uuid.uuid4().hex[:6]}")
    source_id: str
    purpose: str
    data_type: str  # "table" | "timeline" | "numbers" | "entities"
    headers: List[str] = Field(default_factory=list)
    rows: List[List[str]] = Field(default_factory=list)
    markdown: str = ""


# =============================================================================
# ChartArtifact — generated chart code (Mermaid / ECharts / Plotly)
# =============================================================================


class ChartArtifact(BaseModel):
    id: str = Field(default_factory=lambda: f"cht_{uuid.uuid4().hex[:6]}")
    title: str
    purpose: str
    tier: Literal["mermaid", "echarts", "plotly"] = "mermaid"
    chart_code: str = ""
    source_table_id: Optional[str] = None
    source_ids: List[str] = Field(default_factory=list)


# =============================================================================
# Contradiction — a pair of conflicting findings
# =============================================================================


class Contradiction(BaseModel):
    id: str = Field(default_factory=lambda: f"con_{uuid.uuid4().hex[:6]}")
    finding_a_id: str
    finding_b_id: str
    description: str
    resolved: bool = False
    resolution: Optional[str] = None


# =============================================================================
# ResearchNotebook — the in-memory session state
# =============================================================================


class ResearchNotebook(BaseModel):
    query: str
    understanding: Optional[QueryUnderstanding] = None
    subquestions: List[SubQuestion] = Field(default_factory=list)
    sources: List[Source] = Field(default_factory=list)
    findings: List[Finding] = Field(default_factory=list)
    extracted_tables: List[ExtractedTable] = Field(default_factory=list)
    chart_artifacts: List[ChartArtifact] = Field(default_factory=list)
    contradictions: List[Contradiction] = Field(default_factory=list)
    gaps: List[str] = Field(default_factory=list)
    search_history: List[str] = Field(default_factory=list)
    stop_reason: Optional[str] = None

    # ──────────────────── Lookup helpers ────────────────────

    def get_source(self, source_id: str) -> Optional[Source]:
        return next((s for s in self.sources if s.id == source_id), None)

    def source_url_index(self) -> Dict[str, str]:
        return {s.url: s.id for s in self.sources}

    # ──────────────────── Brain context window ────────────────────

    def brain_context_window(self) -> dict:
        """Compact JSON snapshot for the brain's context.

        Contains only what the brain needs to decide next steps:
          - SQ coverage (status, key finding, weakness, source types seen)
          - Open contradictions (description only)
          - Missing authoritative source types globally
          - Search history (queries, not results)
          - Budget/gap summary

        Never contains: raw source text, full finding lists, table cells,
        or chart code — anything the brain has already acted on.
        """
        source_map = {s.id: s for s in self.sources}
        tier1_types = {"official_regulatory", "company_primary", "market_news", "academic"}

        sq_summary = []
        for sq in self.subquestions:
            entry: dict = {
                "id": sq.id,
                "question": sq.question,
                "status": sq.status,
                "confidence": sq.confidence,
            }
            if sq.key_finding:
                entry["key_finding"] = sq.key_finding[:120]
            if sq.weakness:
                entry["weakness"] = sq.weakness
            if sq.source_types_seen:
                entry["source_types"] = sq.source_types_seen
            sq_summary.append(entry)

        open_contras = [
            {"id": c.id, "description": c.description}
            for c in self.contradictions
            if not c.resolved
        ]

        seen_types = {s.evidence_type for s in self.sources}
        missing_auth_types = sorted(tier1_types - seen_types)

        answered = sum(1 for sq in self.subquestions if sq.status == "answered")
        total = len(self.subquestions)
        high_conf = sum(1 for f in self.findings if f.confidence == "high")
        fetched = sum(1 for s in self.sources if s.fetch_success)

        return {
            "subquestions": sq_summary,
            "coverage": f"{answered}/{total} answered",
            "open_contradictions": open_contras,
            "missing_authoritative_source_types": missing_auth_types,
            "search_history": self.search_history,
            "sources_total": len(self.sources),
            "sources_fully_read": fetched,
            "findings_total": len(self.findings),
            "findings_high_confidence": high_conf,
            "tables_extracted": len(self.extracted_tables),
            "charts_generated": len(self.chart_artifacts),
            "gaps": self.gaps,
        }

    # ──────────────────── Deterministic verification ────────────────────

    def run_deterministic_checks(self) -> List[str]:
        """Pure-Python sanity checks. Returns warnings as strings; non-fatal.

        - Findings reference real source IDs and SQ IDs.
        - Contradiction IDs resolve.
        - Sub-questions marked "answered" must have at least one authoritative
          source — otherwise auto-downgrade to "partial" with a weakness note.
        """
        warnings: List[str] = []
        finding_ids = {f.id for f in self.findings}
        source_ids = {s.id for s in self.sources}
        sq_ids = {sq.id for sq in self.subquestions}
        tier1_types = {"official_regulatory", "company_primary", "market_news", "academic"}

        for f in self.findings:
            if f.source_id not in source_ids:
                warnings.append(f"Finding {f.id} references unknown source_id {f.source_id}")
            bad_sqs = [sid for sid in f.sq_ids if sid not in sq_ids]
            if bad_sqs:
                warnings.append(f"Finding {f.id} references unknown sq_ids: {bad_sqs}")

        for c in self.contradictions:
            if c.finding_a_id not in finding_ids:
                warnings.append(
                    f"Contradiction {c.id} references unknown finding_a_id {c.finding_a_id}"
                )
            if c.finding_b_id not in finding_ids:
                warnings.append(
                    f"Contradiction {c.id} references unknown finding_b_id {c.finding_b_id}"
                )

        src_map = {s.id: s for s in self.sources}
        for sq in self.subquestions:
            if sq.status == "answered":
                sq_findings = [f for f in self.findings if sq.id in f.sq_ids]
                has_auth = any(
                    src_map.get(f.source_id, Source(url="", title="")).evidence_type in tier1_types
                    for f in sq_findings
                )
                if not has_auth:
                    sq.status = "partial"
                    sq.weakness = "Downgraded: no authoritative source"
                    warnings.append(
                        f"SQ {sq.id} downgraded from 'answered' to 'partial' — no authoritative source"
                    )

        return warnings

    # ──────────────────── Synthesis brief for the writer ────────────────────

    def synthesis_brief(self, max_findings: int = 200) -> dict:
        """Pre-assemble the report writer's input.

        Numbers sources sequentially, groups findings by sub-question, and
        carries the full notebook into a compact JSON the writer can cite from.
        Never contains raw source text.
        """
        source_map = {s.id: s for s in self.sources}
        tier1_types = {"official_regulatory", "company_primary", "market_news", "academic"}

        # Prioritise findings: tier1 + high → all high → medium → low
        def _priority(f: Finding) -> int:
            src = source_map.get(f.source_id)
            et = src.evidence_type if src else "unknown"
            conf = {"high": 0, "medium": 1, "low": 2}.get(f.confidence, 3)
            tier = 0 if et in tier1_types else 1
            return tier * 10 + conf

        mapped = [f for f in self.findings if f.sq_ids]
        mapped.sort(key=_priority)

        selected: List[Finding] = []
        for f in mapped:
            if len(selected) >= max_findings:
                break
            selected.append(f)

        # Number sources referenced by findings or charts.
        relevant_src_ids = {f.source_id for f in selected}
        for c in self.chart_artifacts:
            relevant_src_ids.update(c.source_ids)
        source_id_to_num: Dict[str, int] = {}
        numbered_sources = []
        for i, src in enumerate(
            [s for s in self.sources if s.id in relevant_src_ids], 1
        ):
            source_id_to_num[src.id] = i
            numbered_sources.append({
                "num": i,
                "title": src.title,
                "url": src.url,
                "evidence_type": src.evidence_type,
                "freshness": src.freshness,
                "fetch_success": src.fetch_success,
            })

        # Group findings by sub-question.
        themes = []
        for sq in self.subquestions:
            sq_findings = [f for f in selected if sq.id in f.sq_ids]
            if not sq_findings:
                continue
            theme_findings = [
                {
                    "text": f.text,
                    "source_num": source_id_to_num.get(f.source_id, 0),
                    "confidence": f.confidence,
                }
                for f in sq_findings
            ]
            themes.append({
                "name": sq.question,
                "status": sq.status,
                "findings": theme_findings,
            })

        # High-confidence unassigned findings as a catch-all theme.
        unassigned = [f for f in self.findings if not f.sq_ids and f.confidence == "high"]
        if unassigned:
            unassigned_entries = []
            for f in unassigned[:30]:
                src_num = source_id_to_num.get(f.source_id, 0)
                if src_num == 0:
                    src = source_map.get(f.source_id)
                    if src:
                        next_num = len(numbered_sources) + 1
                        source_id_to_num[src.id] = next_num
                        numbered_sources.append({
                            "num": next_num, "title": src.title, "url": src.url,
                            "evidence_type": src.evidence_type, "freshness": src.freshness,
                            "fetch_success": src.fetch_success,
                        })
                        src_num = next_num
                unassigned_entries.append({
                    "text": f.text, "source_num": src_num, "confidence": f.confidence,
                })
            if unassigned_entries:
                themes.append({
                    "name": "Additional Research Findings",
                    "status": "supplementary",
                    "findings": unassigned_entries,
                })

        tables_out = [
            {"id": t.id, "purpose": t.purpose, "data_type": t.data_type, "markdown": t.markdown}
            for t in self.extracted_tables
        ]

        charts_out = []
        for c in self.chart_artifacts:
            if not c.chart_code:
                continue
            chart_source_nums = sorted({
                source_id_to_num[sid] for sid in c.source_ids if sid in source_id_to_num
            })
            charts_out.append({
                "id": c.id,
                "title": c.title,
                "tier": c.tier,
                "source_nums": chart_source_nums,
            })

        sq_summary = [
            {
                "question": sq.question,
                "status": sq.status,
                "key_finding": sq.key_finding,
                "weakness": sq.weakness,
            }
            for sq in self.subquestions
        ]

        contras_out = [
            {
                "description": c.description,
                "resolved": c.resolved,
                "resolution": c.resolution,
            }
            for c in self.contradictions
        ]

        return {
            "query": self.query,
            "query_type": self.understanding.query_type if self.understanding else "open-ended",
            "output_format": (
                self.understanding.output_format if self.understanding else "detailed_report"
            ),
            "time_sensitivity": (
                self.understanding.time_sensitivity if self.understanding else "historical_ok"
            ),
            "sources": numbered_sources,
            "themes": themes,
            "tables": tables_out,
            "charts": charts_out,
            "subquestions": sq_summary,
            "contradictions": contras_out,
            "gaps": self.gaps,
            "stop_reason": self.stop_reason,
        }


# =============================================================================
# BudgetManager — hard caps on tool usage
# =============================================================================


class BudgetManager(BaseModel):
    max_react_steps: int = 30
    max_reads_total: int = 25
    max_charts: int = 4

    steps_used: int = 0
    reads_used: int = 0
    charts_used: int = 0
    searches_used: int = 0

    def can_read(self) -> bool:
        return self.reads_used < self.max_reads_total and self.steps_used < self.max_react_steps

    def can_generate_chart(self) -> bool:
        return self.charts_used < self.max_charts

    def record_step(self) -> None:
        self.steps_used += 1

    def record_read(self) -> None:
        self.reads_used += 1

    def record_search(self) -> None:
        self.searches_used += 1

    def record_chart(self) -> None:
        self.charts_used += 1

    def exhausted(self) -> bool:
        return self.steps_used >= self.max_react_steps

    def status_line(self) -> str:
        parts = [
            f"Steps {self.steps_used}/{self.max_react_steps}",
            f"Reads {self.reads_used}/{self.max_reads_total}",
            f"Searches {self.searches_used}",
        ]
        if self.charts_used > 0:
            parts.append(f"Charts {self.charts_used}/{self.max_charts}")
        return " · ".join(parts)
