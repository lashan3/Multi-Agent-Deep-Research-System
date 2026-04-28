"""Structured data extraction — markdown tables (regex) and LLM-based extraction.

The brain calls `extract_data` when it spots numeric data worth structuring.
This module provides:

  1. extract_tables_from_text() — pure regex; finds existing markdown tables.
  2. extract_structured_data()   — LLM-based; turns prose into a normalized table.
  3. table_to_markdown()         — render an ExtractedTable as markdown.
"""

from __future__ import annotations

import json
import re
from typing import List, Optional

from deep_research.config import (
    FAST_MODEL,
    FAST_TEMPERATURE,
    FAST_TIMEOUT,
    SCHEMA_REPAIR_ATTEMPTS,
)
from deep_research.llm import call_llm, strip_code_fences
from deep_research.notebook import ExtractedTable


def extract_tables_from_text(text: str, source_id: str) -> List[ExtractedTable]:
    """Detect existing markdown tables in text. No LLM call."""
    tables: List[ExtractedTable] = []

    table_pattern = re.compile(
        r"(?:^|\n)(\|[^\n]+\|\n(?:\|[-: ]+\|\n)(?:\|[^\n]+\|\n?)+)",
        re.MULTILINE,
    )
    for match in table_pattern.finditer(text):
        raw_table = match.group(1).strip()
        lines = [line.strip() for line in raw_table.split("\n") if line.strip()]
        if len(lines) < 3:
            continue

        headers = [cell.strip() for cell in lines[0].split("|") if cell.strip()]
        rows = []
        for line in lines[2:]:  # skip the separator row
            cells = [cell.strip() for cell in line.split("|") if cell.strip()]
            if cells:
                rows.append(cells)

        if not headers or not rows:
            continue

        tables.append(
            ExtractedTable(
                source_id=source_id,
                purpose="auto-detected from source",
                data_type="table",
                headers=headers,
                rows=rows,
                markdown=raw_table,
            )
        )

    return tables


STRUCTURED_DATA_PROMPT = """\
You are a data extraction specialist for this research query:
Query: {query}

Extract structured {data_type} data from the text below for this purpose: {purpose}

Output valid JSON only.

For data_type "table":
{{
  "data_type": "table",
  "headers": ["col1", "col2", ...],
  "rows": [["val1", "val2", ...], ...],
  "title": "descriptive table title"
}}

For data_type "timeline":
{{
  "data_type": "timeline",
  "headers": ["date", "event", "details"],
  "rows": [["2024-01", "event description", "additional context"], ...],
  "title": "timeline title"
}}

For data_type "numbers":
{{
  "data_type": "numbers",
  "headers": ["metric", "value", "unit", "source", "date"],
  "rows": [["GDP growth", "3.2", "%", "IMF", "2024"], ...],
  "title": "key statistics"
}}

For data_type "entities":
{{
  "data_type": "entities",
  "headers": ["name", "type", "description", "relevance"],
  "rows": [["OpenAI", "company", "AI lab", "key player"], ...],
  "title": "key entities"
}}

Rules:
- Only include data explicitly present in the text
- Normalize units: use consistent formats (e.g., "$B" not "$1,000,000,000")
- If no structured data exists, return {{"data_type": "{data_type}", "headers": [], "rows": [], "title": ""}}
"""


def extract_structured_data(
    text: str,
    source_id: str,
    data_type: str,
    purpose: str,
    query: str,
    api_key: str,
    base_url: str,
    model: str = FAST_MODEL,
) -> Optional[ExtractedTable]:
    """LLM-based structured extraction. Returns ExtractedTable or None."""
    text_capped = text[:8000]
    prompt = STRUCTURED_DATA_PROMPT.format(
        query=query,
        data_type=data_type,
        purpose=purpose,
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
                    "content": f"Extract {data_type} data:\n\n{text_capped}"
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
            data = json.loads(raw)
            headers = data.get("headers", [])
            rows = data.get("rows", [])
            title = data.get("title", purpose)
            if not headers or not rows:
                return None
            markdown = table_to_markdown(headers, rows, title)
            return ExtractedTable(
                source_id=source_id,
                purpose=purpose,
                data_type=data_type,
                headers=headers,
                rows=rows,
                markdown=markdown,
            )
        except (json.JSONDecodeError, Exception) as e:
            if attempt < SCHEMA_REPAIR_ATTEMPTS:
                raw = strip_code_fences(
                    _call(f"Invalid JSON. Error: {e}. Return ONLY valid JSON.")
                )

    return None


def table_to_markdown(headers: List[str], rows: List[List[str]], title: str = "") -> str:
    """Render headers + rows as a GitHub-flavored markdown table."""
    if not headers:
        return ""

    lines = []
    if title:
        lines.append(f"**{title}**\n")

    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")

    for row in rows:
        padded = list(row) + [""] * max(0, len(headers) - len(row))
        padded = padded[: len(headers)]
        lines.append("| " + " | ".join(str(c) for c in padded) + " |")

    return "\n".join(lines)
