"""Configuration — env-var-driven defaults plus a programmatic Config dataclass."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv is optional at runtime
    pass


# =============================================================================
# Module-level defaults (used when DeepResearchAgent is instantiated with no args)
# =============================================================================

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY", "")
PERPLEXITY_SEARCH_URL = "https://api.perplexity.ai/search"
PERPLEXITY_AGENT_URL = "https://api.perplexity.ai/v1/agent"

# Brain — drives the ReAct loop, plan, outline, and report writer.
# Must support tool/function calling via the OpenAI chat-completions schema.
BRAIN_MODEL = os.getenv("BRAIN_MODEL", "anthropic/claude-sonnet-4-5")

# Fast — finding extraction, structured-data extraction, chart code generation.
FAST_MODEL = os.getenv("FAST_MODEL", "google/gemini-2.5-flash")

# Reader — passed to the Perplexity Agent endpoint to fetch full page text.
READER_MODEL = os.getenv("READER_MODEL", "xai/grok-4-fast")

BRAIN_TEMPERATURE = float(os.getenv("BRAIN_TEMPERATURE", "0.2"))
BRAIN_TIMEOUT = int(os.getenv("BRAIN_TIMEOUT", "240"))
FAST_TEMPERATURE = float(os.getenv("FAST_TEMPERATURE", "0.1"))
FAST_TIMEOUT = int(os.getenv("FAST_TIMEOUT", "240"))

MAX_REACT_STEPS = int(os.getenv("MAX_REACT_STEPS", "30"))
MAX_READS = int(os.getenv("MAX_READS", "25"))
MAX_CHARTS = int(os.getenv("MAX_CHARTS", "4"))

MAX_SEARCH_RESULTS = int(os.getenv("MAX_SEARCH_RESULTS", "10"))
MAX_TOKENS_PER_PAGE = int(os.getenv("MAX_TOKENS_PER_PAGE", "8048"))

READER_ENABLED = os.getenv("READER_ENABLED", "true").lower() == "true"
READER_TIMEOUT = int(os.getenv("READER_TIMEOUT", "90"))
READER_MAX_OUTPUT_TOKENS = int(os.getenv("READER_MAX_OUTPUT_TOKENS", "50000"))

SCHEMA_REPAIR_ATTEMPTS = int(os.getenv("SCHEMA_REPAIR_ATTEMPTS", "1"))


# =============================================================================
# Programmatic config — pass to DeepResearchAgent for explicit control
# =============================================================================


@dataclass
class Config:
    """Programmatic configuration for DeepResearchAgent.

    Any field left as None falls back to the corresponding environment variable
    (or its default). This lets you mix-and-match: load most settings from .env
    and override a few in code.
    """

    openai_api_key: Optional[str] = None
    openai_base_url: Optional[str] = None
    perplexity_api_key: Optional[str] = None

    brain_model: Optional[str] = None
    fast_model: Optional[str] = None
    reader_model: Optional[str] = None

    brain_temperature: Optional[float] = None
    brain_timeout: Optional[int] = None

    max_react_steps: Optional[int] = None
    max_reads: Optional[int] = None
    max_charts: Optional[int] = None
    max_search_results: Optional[int] = None
    reader_enabled: Optional[bool] = None

    def resolved(self) -> "ResolvedConfig":
        """Apply env-var fallbacks and return a fully-populated config."""
        return ResolvedConfig(
            openai_api_key=self.openai_api_key or OPENAI_API_KEY,
            openai_base_url=self.openai_base_url or OPENAI_BASE_URL,
            perplexity_api_key=self.perplexity_api_key or PERPLEXITY_API_KEY,
            brain_model=self.brain_model or BRAIN_MODEL,
            fast_model=self.fast_model or FAST_MODEL,
            reader_model=self.reader_model or READER_MODEL,
            brain_temperature=(
                self.brain_temperature if self.brain_temperature is not None else BRAIN_TEMPERATURE
            ),
            brain_timeout=self.brain_timeout if self.brain_timeout is not None else BRAIN_TIMEOUT,
            max_react_steps=(
                self.max_react_steps if self.max_react_steps is not None else MAX_REACT_STEPS
            ),
            max_reads=self.max_reads if self.max_reads is not None else MAX_READS,
            max_charts=self.max_charts if self.max_charts is not None else MAX_CHARTS,
            max_search_results=(
                self.max_search_results
                if self.max_search_results is not None
                else MAX_SEARCH_RESULTS
            ),
            reader_enabled=(
                self.reader_enabled if self.reader_enabled is not None else READER_ENABLED
            ),
        )


@dataclass
class ResolvedConfig:
    """A Config with every field populated. Internal — use Config to construct."""

    openai_api_key: str
    openai_base_url: str
    perplexity_api_key: str

    brain_model: str
    fast_model: str
    reader_model: str

    brain_temperature: float
    brain_timeout: int

    max_react_steps: int
    max_reads: int
    max_charts: int
    max_search_results: int
    reader_enabled: bool
