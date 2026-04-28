"""FastAPI server — serves the web UI and exposes a streaming research endpoint."""

from deep_research.server.app import app

__all__ = ["app"]
