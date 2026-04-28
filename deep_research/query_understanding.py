"""Phase 0 — Query understanding.

A single LLM call that always returns JSON. The JSON includes a needs_clarification
flag. The orchestrator checks the flag:
  - true  → display clarifying questions to the user, wait for follow-up
  - false → proceed to research

On follow-up (the user has answered the clarifying questions), the original
conversation history is sent again so the LLM sees its own JSON + the user's
answers and produces an updated JSON with needs_clarification=false.
"""

from __future__ import annotations

import json
from datetime import date
from typing import List, Optional

from deep_research.config import SCHEMA_REPAIR_ATTEMPTS
from deep_research.llm import call_llm_with_retry, strip_code_fences
from deep_research.notebook import QueryUnderstanding
from deep_research.prompts import QUERY_UNDERSTANDING_PROMPT


def understand_query(
    query: str,
    api_key: str,
    base_url: str,
    model: str,
    timeout: int = 240,
    conversation_history: Optional[List[dict]] = None,
) -> QueryUnderstanding:
    """Parse the user query into a structured QueryUnderstanding."""
    today = date.today().isoformat()

    if conversation_history:
        messages = conversation_history
    else:
        messages = [
            {"role": "system", "content": QUERY_UNDERSTANDING_PROMPT.format(today=today)},
            {"role": "user", "content": query},
        ]

    def _call(extra: str = "") -> str:
        call_messages = messages
        if extra:
            call_messages = messages + [{"role": "user", "content": extra}]
        response = call_llm_with_retry(
            api_key=api_key,
            base_url=base_url,
            model=model,
            messages=call_messages,
            temperature=0.1,
            timeout=timeout,
            max_tokens=6000,
            max_retries=3,
        )
        return response["choices"][0]["message"].get("content", "{}")

    raw = strip_code_fences(_call())

    for attempt in range(SCHEMA_REPAIR_ATTEMPTS + 1):
        try:
            data = json.loads(raw)
            return QueryUnderstanding(
                query_type=data.get("query_type", "open-ended"),
                output_format=data.get("output_format", "detailed_report"),
                time_sensitivity=data.get("time_sensitivity", "historical_ok"),
                domain_hints=data.get("domain_hints", []),
                hidden_subproblems=data.get("hidden_subproblems", []),
                initial_subquestions=data.get("initial_subquestions", []),
                data_likely=bool(data.get("data_likely", False)),
                needs_clarification=bool(data.get("needs_clarification", False)),
                clarifying_questions=data.get("clarifying_questions", []),
                suggested_title=data.get("suggested_title", ""),
                resolved_query=data.get("resolved_query", ""),
            )
        except (json.JSONDecodeError, Exception):
            if attempt < SCHEMA_REPAIR_ATTEMPTS:
                raw = strip_code_fences(
                    _call("That was not valid JSON. Output ONLY the JSON object.")
                )

    # Fallback — research will still run, just without rich understanding metadata.
    return QueryUnderstanding(initial_subquestions=[query])
