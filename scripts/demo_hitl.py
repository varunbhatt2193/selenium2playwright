"""Step 7.2 live demo: the same file, two answers, two different conversions.

    uv run python scripts/demo_hitl.py
    uv run python scripts/demo_hitl.py --answers handler-first expect-event

samples/selenium-suite/pages/AlertsPage.ts accepts one browser dialog and
dismisses another. Playwright can handle those several ways and the choice
changes what the test proves, so the agent stops at `risk_review` and asks
before spending a single token on a guess.

This script plays the human twice. Each arm is its own thread in one SQLite
file: it runs until the graph pauses, prints the question exactly as a person
would see it, resumes that thread with Command(resume=<answer>), and lets the
rest of the run — convert, validate, critic, repairs — proceed. Both arms are
real model calls against the configured S2P_MODEL; nothing is injected.

What to look for: the two output files differ, and each one implements the
answer its arm gave. In LangSmith, the `risk_review` node appears twice per arm
(once when it asked, once when it was resumed) and the `convert` prompt carries
a HUMAN DECISIONS block with that arm's sentence.
"""

from __future__ import annotations

import argparse
import difflib
import json
from pathlib import Path
from uuid import uuid4

from langchain_core.tracers.langchain import wait_for_all_tracers
from langgraph.types import Command
from langsmith import Client, tracing_context

from selenium2playwright import graph, memory, risk

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "samples/selenium-suite/pages/AlertsPage.ts"
OUTPUT = ROOT / "out/7.2"
DEFAULT_ANSWERS = ("handler-first", "expect-event")


def trace_url(run_id) -> str:
    try:
        client = Client()
        return client.get_run_url(run=client.read_run(run_id))
    except Exception as exc:  # a missing trace must not fail the demo itself
        return f"(lookup failed for run {run_id}: {exc})"


def show_question(payload: dict) -> None:
    """Print the interrupt payload the way a front end would render it."""
    print(f"  PAUSED — {payload['title']}", flush=True)
    print(f"    found: {payload['evidence']}", flush=True)
    print(f"    {payload['question']}", flush=True)
    for option in payload["options"]:
        print(f"      - {option['key']}: {option['label']}", flush=True)


def arm(compiled, answer: str, label: str) -> tuple[dict, dict]:
    """One conversation: run, answer every question with `answer`, finish."""
    print(f"\n=== {label}: answering {answer!r} ===", flush=True)
    payload: object = {"source_path": str(SOURCE), "output_path": str(OUTPUT / f"{label}.ts"),
                       "ask_risks": True}
    final: dict = {}
    runs, questions = [], []
    for _ in range(len(risk.RISKS) + 1):
        run_id = uuid4()
        runs.append(str(run_id))
        paused = None
        with tracing_context(enabled=True):
            for update in compiled.stream(
                payload, stream_mode="updates",
                config=memory.thread_config(
                    label, run_id=run_id, run_name=f"hitl-demo-{label}",
                    tags=["step:7.2", f"answer:{answer}"], recursion_limit=14),
            ):
                if "__interrupt__" in update:
                    paused = update["__interrupt__"][0].value
                    continue
                for node, values in update.items():
                    final.update(values)
                    event = {"node": node, "attempt": final.get("iteration", 0)}
                    if node == "intake":
                        event["risks"] = [f"{r.kind}@{r.line}" for r in values["risks"]]
                    if node == "risk_review":
                        event["decisions"] = values.get("decisions", {})
                    if node == "validate":
                        event["gates"] = {r.gate: r.passed for r in values["validation"]}
                    if node == "critic":
                        event["verdict"] = values["critique"].verdict if values["critique"] else "unavailable"
                    if node == "assemble":
                        event["status"] = values["report"].status
                    print(json.dumps(event), flush=True)
        if paused is None:
            break
        questions.append(paused["kind"])
        show_question(paused)
        payload = Command(resume=answer)
    return final, {"label": label, "answer": answer, "runs": runs, "questions_asked": questions}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--answers", nargs=2, default=list(DEFAULT_ANSWERS), metavar="ANSWER",
                        help=f"the two answers to compare (option keys of: {', '.join(risk.RISKS)})")
    args = parser.parse_args(argv)

    OUTPUT.mkdir(parents=True, exist_ok=True)
    db = OUTPUT / "threads.sqlite"
    print(f"source {SOURCE.relative_to(ROOT)} · threads in {db.relative_to(ROOT)}", flush=True)

    results = []
    with memory.open_checkpointer(db) as checkpointer:
        compiled = graph.build_graph(checkpointer)
        for answer in args.answers:
            results.append(arm(compiled, answer, label=answer))

    codes = []
    for state, meta in results:
        report = state["report"]
        code = report.result.code if report.result is not None else ""
        codes.append(code)
        (OUTPUT / f"{meta['label']}.ts").write_text(code, encoding="utf-8")
        (OUTPUT / f"{meta['label']}-report.json").write_text(
            report.model_dump_json(indent=2) + "\n", encoding="utf-8")

    diff = list(difflib.unified_diff(codes[0].splitlines(), codes[1].splitlines(),
                                     fromfile=args.answers[0], tofile=args.answers[1], lineterm=""))
    (OUTPUT / "answers.diff").write_text("\n".join(diff) + "\n", encoding="utf-8")

    wait_for_all_tracers()
    receipt = {
        "source": str(SOURCE.relative_to(ROOT)),
        "arms": [{**meta, "status": state["report"].status, "attempts": state["report"].attempts,
                  "decisions": state.get("decisions", {}),
                  "usage": state.get("usage"), "critic_usage": state.get("critic_usage"),
                  "urls": [trace_url(run) for run in meta["runs"]]}
                 for state, meta in results],
        "code_changed": codes[0] != codes[1],
        "diff_lines": len([line for line in diff if line[:1] in "+-" and line[:3] not in ("---", "+++")]),
    }
    (OUTPUT / "demo-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    print("", flush=True)
    for entry, (state, _) in zip(receipt["arms"], results):
        print(f"{entry['label']}: asked {entry['questions_asked']} → {entry['status']} "
              f"in {entry['attempts']} attempt(s) — {state['report'].reason}", flush=True)
    print(f"answer changed the output: {receipt['code_changed']} "
          f"({receipt['diff_lines']} changed lines)", flush=True)
    print(f"Artifacts: {OUTPUT}", flush=True)
    # The step is done when the pause happened AND the answer changed the code.
    asked = all(entry["questions_asked"] for entry in receipt["arms"])
    return 0 if asked and receipt["code_changed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
