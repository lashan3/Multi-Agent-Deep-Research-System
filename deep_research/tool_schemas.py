"""OpenAI-format tool schemas presented to the brain in the ReAct loop.

The brain can call: search_web, read_url, extract_data, generate_chart,
finish_research. Notebook updates and verification are orchestrator code,
not tools.
"""


SEARCH_WEB_TOOL = {
    "type": "function",
    "function": {
        "name": "search_web",
        "description": (
            "Search the web for information. Issue multiple targeted queries to cover "
            "different angles of the research question. Returns pre-ranked result "
            "summaries with evidence_type (official_regulatory, company_primary, "
            "market_news, analyst_forecast, academic, general_web) and freshness ratings."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "List of specific, narrow search queries. Each query should "
                        "target a DIFFERENT aspect — no overlapping searches. Be "
                        "specific: 'AI chip investment Q1 2025 TSMC NVIDIA' beats 'AI chips'."
                    ),
                },
                "reasoning": {
                    "type": "string",
                    "description": "Why you are issuing these searches — what gap are you filling?",
                },
                "date_range": {
                    "type": "string",
                    "description": (
                        "Optional ISO date range: '2024-01-01..2025-04-07'. Use when "
                        "time_sensitivity = current_data_required."
                    ),
                },
            },
            "required": ["queries", "reasoning"],
        },
    },
}

READ_URL_TOOL = {
    "type": "function",
    "function": {
        "name": "read_url",
        "description": (
            "Fetch the full content of a URL and return extracted findings as "
            "structured JSON. The raw page text is automatically processed — you "
            "receive findings, not HTML. Use for: authoritative primary sources, "
            "detailed reports, official data pages. Do NOT use for every result — "
            "save budget for high-value sources."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to read in full."},
                "reason": {
                    "type": "string",
                    "description": (
                        "Why this specific URL is worth reading in full. What evidence "
                        "do you expect to find here?"
                    ),
                },
            },
            "required": ["url", "reason"],
        },
    },
}

EXTRACT_DATA_TOOL = {
    "type": "function",
    "function": {
        "name": "extract_data",
        "description": (
            "Extract structured data (table, timeline, numbers, entities) from a "
            "source already registered in the research notebook. Use when you've "
            "found numeric data that should be structured for analysis, comparison, "
            "or chart generation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "source_id": {
                    "type": "string",
                    "description": (
                        "The source ID (src_xxx) from the research notebook. Must be a "
                        "source already registered from a read_url call."
                    ),
                },
                "data_type": {
                    "type": "string",
                    "enum": ["table", "timeline", "numbers", "entities"],
                    "description": (
                        "table: rows and columns. timeline: chronological events with "
                        "dates. numbers: key statistics. entities: named entities."
                    ),
                },
                "purpose": {
                    "type": "string",
                    "description": (
                        "What you want from this extraction — e.g. 'extract quarterly "
                        "revenue figures for comparison' or 'extract policy milestone dates'."
                    ),
                },
            },
            "required": ["source_id", "data_type", "purpose"],
        },
    },
}

GENERATE_CHART_TOOL = {
    "type": "function",
    "function": {
        "name": "generate_chart",
        "description": (
            "Generate a chart or visualization from your research data. Provide the "
            "data, chart type, and design requirements. A specialist will create the "
            "chart code — you do NOT write code yourself. The chart will be embedded "
            "in the final report automatically. Use for: timelines, gantt charts, "
            "bar/line/pie charts, radar charts, flowcharts."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Chart title — descriptive and specific.",
                },
                "chart_type": {
                    "type": "string",
                    "description": (
                        "Type of visualization: 'timeline', 'gantt', 'bar', 'line', "
                        "'pie', 'radar', 'flowchart', 'heatmap', 'scatter', 'comparison_matrix'."
                    ),
                },
                "data": {
                    "type": "string",
                    "description": (
                        "The data to visualize — structured text or JSON. Include ALL "
                        "values, labels, categories, and dates. Be complete: every data "
                        "point you want shown."
                    ),
                },
                "design_notes": {
                    "type": "string",
                    "description": (
                        "Optional design preferences: color scheme, what to emphasize, "
                        "groupings, layout. Leave empty for professional defaults."
                    ),
                },
                "source_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "List of source IDs (src_xxx) the chart data comes from. "
                        "Every data point should be traceable to a source. These are "
                        "used for citations in the report."
                    ),
                },
            },
            "required": ["title", "chart_type", "data", "source_ids"],
        },
    },
}

FINISH_RESEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "finish_research",
        "description": (
            "Signal that research is complete and ready for report writing. Call this "
            "when all critical sub-questions have been answered with authoritative "
            "sources, OR when the budget is nearly exhausted. Do NOT call this if "
            "critical sub-questions are still unanswered — search more."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "1-2 sentence summary of what was found.",
                },
                "gaps": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of known gaps for the report's Caveats section.",
                },
            },
            "required": ["summary", "gaps"],
        },
    },
}

ALL_REACT_TOOLS = [
    SEARCH_WEB_TOOL,
    READ_URL_TOOL,
    EXTRACT_DATA_TOOL,
    GENERATE_CHART_TOOL,
    FINISH_RESEARCH_TOOL,
]
