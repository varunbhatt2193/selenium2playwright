"""Step 10.2 — call the deployed graph from your laptop, with langgraph-sdk.

    uv run python scripts/call_deployment.py samples/selenium-suite/pages/LoginPage.ts
    uv run python scripts/call_deployment.py x.ts --url https://<your-deployment>.us.langgraph.app
    uv run python scripts/call_deployment.py x.ts --out out/10.2/x.spec.ts

This is the whole point of the phase in one file. `s2p convert` reads your file,
builds the graph, and runs it in your own process; this reads your file, sends
the **text** to a server somewhere else, and prints what comes back. The agent
is the same agent. Nothing about the conversion happens on this machine.

Why the file is read here and sent as text rather than named as a path: the
server has none of your files. `source_text` is exactly the input step 10.2
added to the graph for this reason, and `source_path` travels beside it as a
*name*, because the classifier, the recall query and the report all want to know
what the file is called — not where it lives on a laptop they cannot see.

The URL is the only thing that changes between a container on your desk and a
deployment in a data centre, which is why this script has no idea which one it
is talking to.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from langgraph_sdk import get_sync_client

# The URL and the key both live in .env now, and this script previously read
# neither — it only ever worked because --url was passed by hand. Loading it
# here is what makes `uv run python scripts/call_deployment.py file.ts` a
# complete command.
load_dotenv()

# `langgraph up` puts the production image on this port; a LangSmith Deployment
# gives you an https URL instead. Both speak the same API, and that is the point.
DEFAULT_URL = os.environ.get("LANGGRAPH_DEPLOYMENT_URL") or "http://127.0.0.1:8123"

GATE_MARK = {True: "PASS", False: "FAIL"}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path, help="the Selenium file to convert, read locally")
    parser.add_argument("--url", default=DEFAULT_URL, help=f"deployment URL (default {DEFAULT_URL})")
    parser.add_argument("--api-key", default=None,
                        help="the deployment's own key; defaults to S2P_API_KEY, "
                             "then LANGSMITH_API_KEY, from the environment")
    parser.add_argument("--assistant", default="convert", help="graph name (convert or suite)")
    parser.add_argument("--out", type=Path, default=None, help="write the converted file here")
    parser.add_argument("--companion", type=Path, action="append", default=[],
                        help="an already-converted companion, sent as text; repeatable")
    parser.add_argument("--thread", default=None,
                        help="reuse a thread id, so a later run can refine this one")
    parser.add_argument("--refine", default="", help="a standing instruction for this run")
    return parser.parse_args(argv)


def payload(args: argparse.Namespace) -> dict:
    """The whole request: text in, no paths the far end could not resolve."""
    return {
        "source_text": args.source.read_text(encoding="utf-8"),
        "source_path": args.source.name,
        "context_text": {p.name: p.read_text(encoding="utf-8") for p in args.companion},
        "refinement": args.refine,
    }


def report_lines(state: dict) -> list[str]:
    """The scorecard, small enough to read in a terminal that is not ours."""
    report = state.get("report")
    if report is None:
        return [f"no report — status {state.get('status', 'unknown')}: {state.get('refusal', '')}"]
    gates = "  ".join(f"{v['gate']}={GATE_MARK[v['passed']]}" for v in report["validation"])
    critique = (report.get("critique") or {}).get("verdict", "unavailable")
    todos = (report.get("result") or {}).get("todos", [])
    lines = [
        f"status   : {report['status']} after {report['attempts']} attempt(s)",
        f"gates    : {gates or '(none ran)'}",
        f"critic   : {critique}",
        f"reason   : {report['reason']}",
        f"models   : {state.get('models', {})}",
    ]
    if todos:
        lines.append(f"todos    : {len(todos)} open TODO(review) item(s)")
        lines += [f"           - {t}" for t in todos]
    return lines


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    # Step 10.4 gave the deployment its own front door. `S2P_API_KEY` is the
    # owner key it checks — unlimited, every graph, server paths allowed — and
    # it is a different thing from `LANGSMITH_API_KEY`, which is what the SDK
    # would otherwise reach for and which the deployment does not accept.
    # Falling back to it anyway, because a deployment with S2P_AUTH=off (a local
    # `langgraph up`, say) does not care what the key is.
    api_key = args.api_key or os.environ.get("S2P_API_KEY") or os.environ.get("LANGSMITH_API_KEY")
    client = get_sync_client(url=args.url, api_key=api_key)
    print(f"→ {args.url}  ({args.assistant})", file=sys.stderr)

    thread_id = args.thread
    if thread_id is None and args.refine:
        # A refinement is a second turn, and a turn needs somewhere to be the
        # second of. The server owns the checkpointer, so the thread is created
        # over the wire rather than in a local SQLite file.
        thread_id = client.threads.create()["thread_id"]
        print(f"  thread {thread_id}", file=sys.stderr)

    final: dict = {}
    for chunk in client.runs.stream(thread_id, args.assistant, input=payload(args),
                                    stream_mode=["updates", "values"]):
        if chunk.event == "updates" and isinstance(chunk.data, dict):
            for node in chunk.data:
                print(f"  · {node}", file=sys.stderr)
        elif chunk.event == "values" and isinstance(chunk.data, dict):
            final = chunk.data

    print("", file=sys.stderr)
    for line in report_lines(final):
        print(line, file=sys.stderr)

    code = (final.get("report") or {}).get("result", {}) or {}
    converted = code.get("code", "")
    if args.out and converted:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(converted, encoding="utf-8")
        print(f"\nwrote {args.out}", file=sys.stderr)
    elif converted:
        # Same contract as `s2p convert`: stdout is the file and nothing else.
        print(converted, end="")

    status = (final.get("report") or {}).get("status")
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
