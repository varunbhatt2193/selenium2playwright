"""Step 7.3 live demo: taught once in one conversation, applied by itself in another.

    caffeinate -i -s uv run python scripts/demo_store.py

Three conversations, in order, against real models:

  1. TEACH     convert pages/LoginPage.ts in thread "monday" and, while there,
               teach one preference the way --remember does mid-conversion.
               Three more are filed straight into the store the way a bare
               `--remember` does: the same rule worded vaguely, one about page
               objects, and one about the CI pipeline that has nothing to do
               with converting a file.
  2. CONTROL   convert tests/upload.spec.ts with no store at all. This is what
               the agent produces on its own.
  3. RECALL    convert the same tests/upload.spec.ts in a brand new thread
               "wednesday", given no instruction of any kind. The only
               difference from the control is that long-term memory exists.

What to look for: the recall arm's prompt carries a REMEMBERED PREFERENCES
block that nobody typed this run, the two output files differ, and three of the
four memories stay behind — including the vaguely worded twin of the one that
was recalled. The scores that decided all of that are printed and kept in the
receipt.

Both conversions are real model calls against the configured S2P_MODEL, and the
embeddings are the configured S2P_EMBEDDINGS. Nothing is injected.

Artifacts: out/7.3/.
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path
from uuid import uuid4

from langchain_core.tracers.langchain import wait_for_all_tracers
from langsmith import Client, tracing_context

from selenium2playwright import env, graph, llm, memory, store

ROOT = Path(__file__).resolve().parents[1]
TAUGHT_ON = ROOT / "samples/selenium-suite/pages/LoginPage.ts"
LATER = ROOT / "samples/selenium-suite/tests/upload.spec.ts"
# The spec imports its page object, so the already-converted one travels with it
# (the companion mechanism from Phase 4). Without it nothing can compile, and a
# failing compile gate would drown out what this demo is actually about.
COMPANION = ROOT / "samples/playwright-golden/pages/UploadPage.ts"
OUTPUT = ROOT / "out/7.3"
USER = "demo"

# Four memories, chosen to show all three outcomes at once. The first is a real
# preference this team has and the playbook does not cover. The second says the
# same thing in vaguer words, to show that how a memory is worded decides whether
# it comes back. The third is about page objects, so it should stay behind on a
# test spec. The fourth is about the CI pipeline and has no business in any
# conversion prompt.
LESSONS = [
    "In test specs, wrap each action in a named test.step() block",
    "Wrap the actions inside every test in test.step() calls named after what the "
    "step does, so the HTML report reads like a scenario",
    "Name page object classes <Feature>Page and give each one an open() method",
    "Our CI publishes the Playwright HTML report to S3 after every nightly run",
]


def trace_url(run_id) -> str:
    try:
        client = Client()
        return client.get_run_url(run=client.read_run(run_id))
    except Exception as exc:  # a missing trace must not fail the demo itself
        return f"(lookup failed for run {run_id}: {exc})"


def convert(compiled, source: Path, label: str, thread: str | None = None,
            remember: str = "", companion: Path | None = None) -> tuple[dict, dict]:
    """One conversation, streamed, with the interesting state printed as it happens."""
    print(f"\n=== {label}: {source.name}"
          + (f" · thread {thread}" if thread else " · no thread")
          + (f" · teaching {remember[:40]!r}…" if remember else "") + " ===", flush=True)
    destination = OUTPUT / ("tests" if companion else "") / f"{label}.ts"
    payload: dict = {"source_path": str(source), "user_id": USER,
                     "output_path": str(destination)}
    if companion:
        payload["context_paths"] = [str(OUTPUT / "pages" / companion.name)]
    if remember:
        payload["remember"] = remember
    final: dict = {}
    run_id = uuid4()
    config = {"run_id": run_id, "run_name": f"store-demo-{label}",
              "tags": ["step:7.3", f"arm:{label}"], "recursion_limit": 14}
    if thread:
        config = memory.thread_config(thread, **config)
    with tracing_context(enabled=True):
        for update in compiled.stream(payload, stream_mode="updates", config=config):
            for node, values in update.items():
                # A node that changed nothing (risk_review with nothing to ask)
                # streams as None, not an empty dict.
                values = values or {}
                final.update(values)
                event = {"node": node, "attempt": final.get("iteration", 0)}
                if node == "recall" and values:
                    event["recalled"] = [f"{m.key}@{m.score:.3f}" if m.score is not None
                                         else f"{m.key}@given-now" for m in values["recalled"]]
                    event["known"] = values["memory_count"]
                if node == "validate":
                    event["gates"] = {r.gate: r.passed for r in values["validation"]}
                if node == "critic":
                    event["verdict"] = values["critique"].verdict if values["critique"] else "unavailable"
                if node == "assemble":
                    event["status"] = values["report"].status
                print(json.dumps(event), flush=True)
    return final, {"label": label, "run": str(run_id), "output": str(destination),
                   "recalled": [{"key": m.key, "score": m.score, "text": m.text}
                                for m in final.get("recalled", [])]}


def write(state: dict, meta: dict) -> str:
    report = state["report"]
    code = report.result.code if report.result is not None else ""
    destination = Path(meta["output"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(code, encoding="utf-8")
    (OUTPUT / f"{meta['label']}-report.json").write_text(report.model_dump_json(indent=2) + "\n",
                                                         encoding="utf-8")
    return code


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    db, threads = OUTPUT / "memories.sqlite", OUTPUT / "threads.sqlite"
    for stale in (db, threads):  # a demo starts from nothing, or it proves nothing
        stale.unlink(missing_ok=True)
    (OUTPUT / "pages").mkdir(exist_ok=True)
    (OUTPUT / "pages" / COMPANION.name).write_text(COMPANION.read_text(encoding="utf-8"),
                                                   encoding="utf-8")
    embeddings = llm.make_embeddings()
    dims = llm.embedding_dims(embeddings)
    print(f"memories in {db.relative_to(ROOT)} · embeddings {env.embeddings_name()} ({dims} dims)",
          flush=True)

    results = {}
    with store.open_store(db, embeddings, dims) as memories:
        with memory.open_checkpointer(threads) as checkpointer:
            taught = graph.build_graph(checkpointer, memories)
            # One lesson taught mid-conversion (what --remember does on a run)...
            results["taught"] = convert(taught, TAUGHT_ON, "taught", thread="monday",
                                        remember=LESSONS[0])
            # ...and two filed with no conversion at all (a bare --remember).
            for lesson in LESSONS[1:]:
                saved = store.remember(memories, lesson, USER, source="demo")
                print(f'  remembered [{saved.key}] {saved.text}', flush=True)
            # No store at all: the same graph every earlier phase runs.
            results["control"] = convert(graph.build_graph(), LATER, "control", companion=COMPANION)
            # A brand new thread, told nothing. Only the store is different.
            results["recall"] = convert(taught, LATER, "recall", thread="wednesday",
                                        companion=COMPANION)
        known = store.memories(memories, USER)

    codes = {label: write(state, meta) for label, (state, meta) in results.items()}
    diff = list(difflib.unified_diff(codes["control"].splitlines(), codes["recall"].splitlines(),
                                     fromfile="control", tofile="recall", lineterm=""))
    (OUTPUT / "recall.diff").write_text("\n".join(diff) + "\n", encoding="utf-8")

    recalled = results["recall"][1]["recalled"]
    wait_for_all_tracers()
    receipt = {
        "embeddings": env.embeddings_name(), "dims": dims,
        "min_score": store.MIN_SCORE, "recall_limit": store.RECALL_LIMIT,
        "taught_on": str(TAUGHT_ON.relative_to(ROOT)), "later": str(LATER.relative_to(ROOT)),
        "memories": [{"key": m.key, "text": m.text} for m in known],
        "arms": [{**meta, "status": state["report"].status, "attempts": state["report"].attempts,
                  "usage": state.get("usage"), "critic_usage": state.get("critic_usage"),
                  "url": trace_url(meta["run"])}
                 for state, meta in results.values()],
        "recall_changed_the_output": codes["control"] != codes["recall"],
        "diff_lines": len([l for l in diff if l[:1] in "+-" and l[:3] not in ("---", "+++")]),
        "noise_recalled": any(m["text"] == LESSONS[-1] for m in recalled),
    }
    (OUTPUT / "demo-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    print("", flush=True)
    for entry, (state, _) in zip(receipt["arms"], results.values()):
        print(f"{entry['label']}: {entry['status']} in {entry['attempts']} attempt(s) — "
              f"{state['report'].reason}", flush=True)
    print(f"\n{len(known)} preference(s) remembered; the fresh conversation recalled "
          f"{len(recalled)}:", flush=True)
    for item in recalled:
        print(f"  {item['score']:.3f}  {item['text']}", flush=True)
    print(f"CI memory (noise) recalled: {receipt['noise_recalled']}", flush=True)
    print(f"memory changed the output: {receipt['recall_changed_the_output']} "
          f"({receipt['diff_lines']} changed lines)", flush=True)
    print(f"Artifacts: {OUTPUT}", flush=True)
    # Done when a fresh conversation recalled something it was never told, that
    # something was not the noise, and it visibly changed the file.
    return 0 if recalled and not receipt["noise_recalled"] and receipt["recall_changed_the_output"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
