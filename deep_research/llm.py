"""LLM call helpers — non-streaming, retrying, and streaming flavours.

All calls go through any OpenAI-compatible /chat/completions endpoint. The base
URL and key are passed in by the caller; no global state.
"""

from __future__ import annotations

import json
import time
from typing import Generator, List, Optional

import requests


def call_llm(
    api_key: str,
    base_url: str,
    model: str,
    messages: List[dict],
    tools: Optional[list] = None,
    temperature: float = 0.3,
    timeout: int = 120,
    max_tokens: Optional[int] = None,
) -> dict:
    """Non-streaming chat completion. Returns the raw response dict."""
    if not api_key:
        raise ValueError("OPENAI_API_KEY is not set")

    payload: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    if max_tokens:
        payload["max_tokens"] = max_tokens

    resp = requests.post(
        f"{base_url}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=(30, timeout),
    )

    if resp.status_code != 200:
        raise RuntimeError(f"{model} API {resp.status_code}: {resp.text[:300]}")

    return resp.json()


def call_llm_with_retry(
    api_key: str,
    base_url: str,
    model: str,
    messages: List[dict],
    tools: Optional[list] = None,
    temperature: float = 0.3,
    timeout: int = 120,
    max_tokens: Optional[int] = None,
    max_retries: int = 3,
    backoff_base: float = 2.0,
) -> dict:
    """call_llm with exponential backoff on transient errors.

    Retries on:
      - requests.Timeout / ReadTimeout
      - requests.ConnectionError
      - HTTP 5xx server errors

    Re-raises 4xx errors immediately — auth/validation issues won't fix themselves.
    """
    last_exc: Exception = RuntimeError("No attempts made")

    for attempt in range(max_retries + 1):
        try:
            return call_llm(
                api_key=api_key,
                base_url=base_url,
                model=model,
                messages=messages,
                tools=tools,
                temperature=temperature,
                timeout=timeout,
                max_tokens=max_tokens,
            )
        except (
            requests.Timeout,
            requests.ConnectionError,
            requests.exceptions.ChunkedEncodingError,
        ) as exc:
            last_exc = exc
        except RuntimeError as exc:
            msg = str(exc)
            if any(f" {code}:" in msg for code in ("400", "401", "403", "404", "422")):
                raise
            last_exc = exc
        except Exception as exc:
            last_exc = exc

        if attempt < max_retries:
            time.sleep(backoff_base ** attempt)

    raise last_exc


def stream_llm(
    api_key: str,
    base_url: str,
    model: str,
    messages: List[dict],
    temperature: float = 0.3,
    max_tokens: int = 16000,
    timeout: int = 300,
) -> Generator[str, None, None]:
    """Streaming chat completion. Yields content chunks (strings)."""
    if not api_key:
        yield "[error] OPENAI_API_KEY is not set"
        return

    resp = requests.post(
        f"{base_url}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        },
        stream=True,
        timeout=(30, timeout),
    )

    if resp.status_code != 200:
        yield f"[error] LLM streaming failed ({resp.status_code}): {resp.text[:200]}"
        return

    done_received = False
    for line in resp.iter_lines():
        if not line:
            continue
        line_str = line.decode("utf-8", errors="replace")
        if not line_str.startswith("data: "):
            continue
        data_str = line_str[6:]
        if data_str.strip() == "[DONE]":
            done_received = True
            break
        try:
            chunk = json.loads(data_str)
            content = chunk["choices"][0]["delta"].get("content", "")
            if content:
                yield content
        except (json.JSONDecodeError, KeyError, IndexError):
            continue

    if not done_received:
        yield "\n\n*Stream interrupted — response may be incomplete.*\n"


def strip_code_fences(text: str) -> str:
    """Remove ```json ... ``` wrappers from LLM output."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[-1].strip() == "```":
            lines = lines[1:-1]
        else:
            lines = lines[1:]
        text = "\n".join(lines)
    return text


def window_brain_messages(messages: List[dict]) -> List[dict]:
    """Pass-through window. Kept as a hook in case future versions want to trim.

    The brain sees the full ReAct trace from the start of the loop. This costs
    more tokens but gives the brain perfect continuity — no orphaned tool
    results, no context resets. Modern Sonnet/Opus context windows comfortably
    handle 15–25 research steps. The orchestrator catches context-length errors
    gracefully if a session exceeds that.
    """
    return messages
