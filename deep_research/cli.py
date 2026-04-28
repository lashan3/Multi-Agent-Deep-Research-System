"""Command-line entry point.

Two modes:

    deep-research "your query"          # run a single query, stream to stdout
    deep-research serve                  # run the web UI (FastAPI + uvicorn)
"""

from __future__ import annotations

import argparse
import os
import sys

from deep_research import DeepResearchAgent
from deep_research.config import Config


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="deep-research",
        description="Multi-agent ReAct research with cited reports.",
    )

    sub = parser.add_subparsers(dest="command")

    # ── serve ───────────────────────────────────────────────────────
    serve_p = sub.add_parser("serve", help="Run the web UI server.")
    serve_p.add_argument("--host", default=os.getenv("HOST", "0.0.0.0"))
    serve_p.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))

    # ── default: a query ────────────────────────────────────────────
    parser.add_argument("query", nargs="?", help="The research question.")
    parser.add_argument(
        "--save", metavar="FILE",
        help="Save the report to a file (also streams to stdout).",
    )
    parser.add_argument(
        "--brain-model", help="Override BRAIN_MODEL for this run.",
    )
    parser.add_argument(
        "--fast-model", help="Override FAST_MODEL for this run.",
    )
    parser.add_argument("--max-steps", type=int, help="Override MAX_REACT_STEPS.")
    parser.add_argument("--max-reads", type=int, help="Override MAX_READS.")
    parser.add_argument("--max-charts", type=int, help="Override MAX_CHARTS.")
    parser.add_argument(
        "--no-clarify",
        action="store_true",
        help="Skip the clarification step even if the model thinks it would help.",
    )

    args = parser.parse_args()

    if args.command == "serve":
        _serve(args.host, args.port)
        return

    if not args.query:
        parser.print_help()
        sys.exit(1)

    config = Config(
        brain_model=args.brain_model,
        fast_model=args.fast_model,
        max_react_steps=args.max_steps,
        max_reads=args.max_reads,
        max_charts=args.max_charts,
    )
    agent = DeepResearchAgent(config)

    output_buffer: list[str] = []
    try:
        for chunk in agent.research(args.query, allow_clarification=not args.no_clarify):
            sys.stdout.write(chunk)
            sys.stdout.flush()
            if args.save:
                output_buffer.append(chunk)
    except KeyboardInterrupt:
        sys.stdout.write("\n[interrupted]\n")
        sys.exit(130)

    if args.save:
        with open(args.save, "w", encoding="utf-8") as f:
            f.write("".join(output_buffer))
        sys.stdout.write(f"\n\n[saved to {args.save}]\n")


def _serve(host: str, port: int) -> None:
    """Run the FastAPI app with uvicorn."""
    try:
        import uvicorn

        from deep_research.server.app import app
    except ImportError as e:
        sys.stderr.write(f"Server dependencies missing: {e}\n")
        sys.stderr.write("Re-run `pip install -e .` from the repo root.\n")
        sys.exit(1)

    sys.stdout.write(f"Starting Deep Research Agent on http://{host}:{port}\n")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
