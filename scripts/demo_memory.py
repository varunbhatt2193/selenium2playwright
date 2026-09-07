"""Step 7.1 live demo: two turns of one conversation, the file named once.

    uv run python scripts/demo_memory.py
    uv run python scripts/demo_memory.py --refine "expose every locator as a readonly field"

Turn 1 converts samples/selenium-suite/pages/LoginPage.ts normally.
Turn 2 supplies *only* a thread id and one sentence of instruction — no source
path, no previous output, nothing re-pasted. Everything else comes back out of
the SQLite checkpointer. Both turns are real model calls against the configured
S2P_MODEL; nothing is injected (unlike demo_reflection.py, which seeds a bug).

What to look for in the two LangSmith traces: the second one's `convert` node
carries the standing instruction and a <previous_conversion> block, and its
`intake` starts at turn 2 with iteration reset to 0.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import uuid4

from langchain_core.tracers.langchain import wait_for_all_tracers
from langsmith import Client, tracing_context

from selenium2playwright import graph, memory

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INSTRUCTION = ("Use getByTestId() for every form field locator, with the test id "
                       "equal to the original element id.")


def trace_url(run_id) -> str:
    try:
        client = Client()
        return client.get_run_url(run=client.read_run(run_id))
    except Exception as exc:  # a missing trace must not fail the demo itself
        return f"(lookup failed for run {run_id}: {exc})"


def turn(compiled, thread: str, inputs: dict, label: str) -> tuple[dict, dict]:
    """One CLI turn's worth of work, with its own trace."""
    run_id = uuid4()
    print(f"\n=== {label} (run {run_id}) ===", flush=True)
    final: dict = {}
    with tracing_context(enabled=True):
        for update in compiled.stream(
            inputs, stream_mode="updates",
            config=memory.thread_config(
                thread, run_id=run_id, run_name=f"memory-demo-{label}",
                tags=["step:7.1", f"turn:{label}"], recursion_limit=14),
        ):
            for node, values in update.items():
                final.update(values)
                event = {"node": node, "turn": final.get("turn"), "attempt": final.get("iteration", 0)}
                if node == "intake":
                    event["conventions"] = values["conventions"]
                    event["from_previous_turn"] = values["baseline"] is not None
                if node == "validate":
                    event["gates"] = {r.gate: r.passed for r in values["validation"]}
                if node == "critic":
                    event["verdict"] = values["critique"].verdict if values["critique"] else "unavailable"
                if node == "assemble":
                    event["status"] = values["report"].status
                print(json.dumps(event), flush=True)
    return final, {"label": label, "run_id": str(run_id)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refine", default=DEFAULT_INSTRUCTION, help="the turn-2 instruction")
    parser.add_argument("--thread", default=f"demo-{uuid4().hex[:8]}", help="thread id (default: fresh)")
    args = parser.parse_args(argv)

    source = ROOT / "samples/selenium-suite/pages/LoginPage.ts"
    output = ROOT / "out/7.1"
    output.mkdir(parents=True, exist_ok=True)
    db = output / "threads.sqlite"
    print(f"thread {args.thread!r} in {db}", flush=True)

    with memory.open_checkpointer(db) as checkpointer:
        compiled = graph.build_graph(checkpointer)
        first, first_run = turn(compiled, args.thread,
                                {"source_path": str(source), "output_path": str(output / "LoginPage.ts")},
                                "turn1-convert")
        # Everything turn 2 is told. No path, no code, no repetition.
        second, second_run = turn(compiled, args.thread, {"refinement": args.refine}, "turn2-refine")

    for name, state in (("turn1", first), ("turn2", second)):
        (output / f"{name}.ts").write_text(state["report"].result.code, encoding="utf-8")
        (output / f"{name}-report.json").write_text(
            state["report"].model_dump_json(indent=2) + "\n", encoding="utf-8")

    wait_for_all_tracers()
    receipt = {
        "thread": args.thread, "instruction": args.refine,
        "turn1": {**first_run, "status": first["report"].status, "attempts": first["report"].attempts,
                  "conventions": first["conventions"], "url": trace_url(first_run["run_id"])},
        "turn2": {**second_run, "status": second["report"].status, "attempts": second["report"].attempts,
                  "conventions": second["conventions"], "url": trace_url(second_run["run_id"])},
        "code_changed": first["report"].result.code != second["report"].result.code,
        "usage": {"turn1": first["usage"], "turn2": second["usage"]},
        "critic_usage": {"turn1": first["critic_usage"], "turn2": second["critic_usage"]},
    }
    (output / "demo-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    print(f"\nturn 1: {first['report'].status} — {first['report'].reason}", flush=True)
    print(f"turn 2: {second['report'].status} — {second['report'].reason}", flush=True)
    print(f"instruction remembered: {second['conventions']}", flush=True)
    print(f"code changed between turns: {receipt['code_changed']}", flush=True)
    print(f"traces:\n  {receipt['turn1']['url']}\n  {receipt['turn2']['url']}", flush=True)
    print(f"Artifacts: {output}", flush=True)
    # The point of the step is that turn 2 refined turn 1's file, not that the
    # model happened to produce a clean run; report both, fail only on neither.
    return 0 if receipt["code_changed"] and second["turn"] == 2 else 1


if __name__ == "__main__":
    raise SystemExit(main())
