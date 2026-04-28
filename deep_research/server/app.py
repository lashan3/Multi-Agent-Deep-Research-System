"""FastAPI app — single-page web UI + Server-Sent Events research stream.

Endpoints:
  GET  /                  → static index.html (the UI)
  GET  /static/{file}     → static assets (CSS, JS)
  POST /api/research      → SSE stream of markdown chunks
  GET  /api/health        → liveness probe
"""

from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from deep_research import DeepResearchAgent
from deep_research.config import Config


_STATIC_DIR = Path(__file__).parent / "static"


app = FastAPI(
    title="Deep Research Agent",
    description="Multi-agent ReAct research with cited reports.",
    version="0.1.0",
)

if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


# Single thread pool for running the synchronous research generator off the
# asyncio event loop. Each in-flight request gets its own thread.
_executor = ThreadPoolExecutor(max_workers=8)


class ResearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    brain_model: Optional[str] = None
    fast_model: Optional[str] = None
    max_react_steps: Optional[int] = None
    max_reads: Optional[int] = None
    max_charts: Optional[int] = None
    allow_clarification: bool = True


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/")
def index() -> FileResponse:
    if not _STATIC_DIR.exists():
        raise HTTPException(status_code=500, detail="Static UI not bundled.")
    return FileResponse(_STATIC_DIR / "index.html")


@app.post("/api/research")
async def research(payload: ResearchRequest):
    """Stream a research run as Server-Sent Events.

    Each event is a JSON object: `{"type": "chunk", "content": "..."}` or
    `{"type": "done"}` at the end. Errors are streamed as
    `{"type": "error", "content": "..."}` followed by `done`.
    """
    config = Config(
        brain_model=payload.brain_model,
        fast_model=payload.fast_model,
        max_react_steps=payload.max_react_steps,
        max_reads=payload.max_reads,
        max_charts=payload.max_charts,
    ).resolved()

    if not config.openai_api_key:
        return JSONResponse(
            {"error": "OPENAI_API_KEY is not set on the server."},
            status_code=400,
        )
    if not config.perplexity_api_key:
        return JSONResponse(
            {"error": "PERPLEXITY_API_KEY is not set on the server."},
            status_code=400,
        )

    agent = DeepResearchAgent(config)
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def _produce() -> None:
        try:
            for chunk in agent.research(
                payload.query, allow_clarification=payload.allow_clarification
            ):
                asyncio.run_coroutine_threadsafe(
                    queue.put({"type": "chunk", "content": chunk}), loop
                )
        except Exception as e:
            asyncio.run_coroutine_threadsafe(
                queue.put({"type": "error", "content": str(e)}), loop
            )
        finally:
            asyncio.run_coroutine_threadsafe(queue.put({"type": "done"}), loop)

    _executor.submit(_produce)

    async def _events():
        while True:
            event = await queue.get()
            yield f"data: {json.dumps(event)}\n\n"
            if event.get("type") == "done":
                break

    return StreamingResponse(
        _events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
