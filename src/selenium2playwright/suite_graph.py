"""Step 9.2 — one graph, many files: fan-out with `Send`, results gathered by a reducer.

Step 9.1 read the folder and produced a plan: which files can be converted, and
in what order (waves). This module runs that plan.

The naive way to convert twelve files is a for-loop around the single-file
graph. It works, and it is wrong for three reasons: nothing is parallel, so
twelve files cost twelve round trips end to end; the trace is twelve unrelated
runs instead of one; and the order is a property of the loop rather than of the
suite. This is the LangGraph way instead, and it is built out of exactly three
ideas — the three this step exists to teach:

  Send — a conditional edge normally returns the *name* of the next node. It
         may instead return a **list of `Send(node, payload)`**, and then
         LangGraph starts one copy of that node per Send, each with its own
         payload as its input, all in the same super-step. That is fan-out: the
         number of branches is decided at run time, from data, and nothing in
         the wiring had to know it. In sync mode LangGraph runs those copies on
         a thread pool, so six files waiting on six model replies wait once.

  reducer — parallel branches all finish by writing to the same state key, and
         LangGraph's default rule is "one value per key per step", so two
         branches writing `outcomes` is an error, not a merge. A reducer is the
         function that says how to combine them: `Annotated[list, operator.add]`
         means "append what each branch returned". It is the join half of the
         fan-out, and without it this graph does not run at all
         (tests/test_suite_graph.py proves that, rather than asserting it).

  subgraph — the per-file work is not new code. It is the Phase 0-8 graph
         (intake → recall → risk_review → convert → validate → critic →
         assemble, up to three laps), invoked inside the `convert_file` node.
         Calling it rather than inlining it keeps one definition of "convert a
         file", and LangSmith nests the child run under this one, so the trace
         reads suite → file → attempt → model call.

The shape:

    START → plan ─→ next_wave ──dispatch──→ [convert_file × N] ──┐
                        ↑                                        │
                        └────────────────────────────────────────┘
                                    ↓ (no waves left)
                                  finish → END

`next_wave` bumps a counter; the conditional edge on it either fans out that
wave or ends the loop. Every file in a wave is independent by construction —
that is what 9.1's topological sort bought — so the parallelism is safe, and
wave N+1 cannot start until every file in wave N is written, which is what
makes the converted page object available as context to the test that imports it.

Not in this step (they are 9.3): compiling the finished tree as one project, the
aggregate scorecard, the parity ledger, the consolidated TODO(review) ledger and
the suite report markdown. This module produces the per-file outcomes those are
built from.
"""

from __future__ import annotations

import operator
import shutil
import time
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Annotated, Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.store.base import BaseStore
from langgraph.types import Send

from selenium2playwright import graph as single
from selenium2playwright import suite
from selenium2playwright.reflection import MAX_ATTEMPTS, sum_usage
from selenium2playwright.schemas import ConversionReport

# Versioned like every other artifact this project emits. It is deliberately not
# called "suite-report": that name belongs to 9.3's document, which adds the
# whole-tree compile and the ledgers on top of these per-file outcomes.
RUN_SCHEMA = "s2p.suite-run/v1"

# Gate order is fixed by the graph; naming it here keeps the table, the JSON and
# the tests reading the same four columns in the same order.
GATES = ("compile", "residue", "lint", "parity")


@dataclass(frozen=True)
class SuiteSettings:
    """The suite run's context: how it runs, not what it is about.

    Same argument as graph.RunSettings, one level up — models and the lap budget
    are choices made per invocation, so they are passed as context and saved
    nowhere. They are handed down to each per-file subgraph unchanged, which is
    why `--model opus` on a suite means opus for all twelve files and not for
    the first one only.
    """

    model: str = ""
    critic_model: str = ""
    max_attempts: int | None = None
    user_id: str = ""  # whose long-term memories the per-file graph may recall


def settings(runtime: Runtime[SuiteSettings] | None) -> SuiteSettings:
    """This run's context, or the defaults — LangGraph does not fill them in."""
    context = getattr(runtime, "context", None)
    if isinstance(context, dict):
        return SuiteSettings(**context)
    return context if isinstance(context, SuiteSettings) else SuiteSettings()


@dataclass(frozen=True)
class FileOutcome:
    """What happened to one file. One of these per Send, collected by the reducer."""

    path: str  # relative to the suite root, the same id 9.1 uses
    wave: int
    status: str  # passed | needs-review | refused | failed
    attempts: int
    reason: str
    gates: tuple[tuple[str, bool], ...] = ()  # (gate, passed), in GATES order
    critic: str = ""  # pass | revise | "" when the review was unavailable
    todos: tuple[str, ...] = ()
    written: str = ""  # where the converted file went; "" when nothing was produced
    seconds: float = 0.0
    usage: dict | None = None
    critic_usage: dict | None = None
    errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status == "passed"


class FileJob(TypedDict, total=False):
    """One Send's payload — and therefore the whole input state of one branch.

    A Send does not merge its payload into the parent state: the node is started
    with exactly this dict, which is why `convert_file` can be read as an
    ordinary function of one file's job rather than of the whole suite.
    """

    path: str
    wave: int
    source_path: str
    output_path: str
    context_paths: list[str]


class SuiteState(TypedDict, total=False):
    """Everything the suite graph knows. `outcomes` is the only reduced key."""

    # inputs
    root: str
    out_root: str
    only: list[str]  # glob patterns; empty means the whole manifest
    # filled by plan
    manifest: suite.Manifest
    waves: list[list[str]]  # after --only filtering; the list the dispatcher walks
    copied: list[str]  # support files carried across untouched
    started: float
    # the loop counter: which wave is being dispatched, 1-based
    wave: int
    # the join. operator.add is what lets N parallel branches write one key:
    # each returns a one-item list and LangGraph concatenates them. Note the
    # consequence — a reduced channel can only be appended to, never rewritten,
    # so the sort into a stable order happens where it is read (see `ordered`).
    outcomes: Annotated[list[FileOutcome], operator.add]
    # filled by finish
    elapsed: float


def plan(state: SuiteState) -> SuiteState:
    """Scan the folder, carry the support files across, and settle the wave list.

    No model runs here. The support files are copied now rather than at the end
    because they are *context*: `tests/login.spec.ts` imports `../support/users`,
    and the converted file has to compile against something.
    """
    root = Path(state["root"])
    out_root = Path(state["out_root"])
    manifest = state.get("manifest") or suite.scan(root)
    patterns = list(state.get("only", []))

    copied = []
    for item in manifest.files:
        if item.action == suite.COPY:
            target = out_root / item.path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / item.path, target)
            copied.append(item.path)

    waves = [[p for p in wave if selected(p, patterns)] for wave in manifest.waves]
    return {"manifest": manifest, "waves": [w for w in waves if w], "copied": copied,
            "wave": 0, "started": time.time()}


def selected(path: str, patterns: list[str]) -> bool:
    """Does --only cover this file? No patterns means everything.

    A pattern matches the relative path (`pages/*.ts`) or the bare name
    (`LoginPage.ts`), because both are what a person types.
    """
    return not patterns or any(fnmatch(path, p) or fnmatch(Path(path).name, p) for p in patterns)


def next_wave(state: SuiteState) -> SuiteState:
    """The loop counter, and the join point every fanned-out branch returns to."""
    return {"wave": state.get("wave", 0) + 1}


def dispatch(state: SuiteState) -> list[Send] | str:
    """The fan-out itself: one Send per file in this wave, or "finish" when done.

    This is an ordinary conditional edge — the only unusual thing about it is
    what it returns. A name routes to one node; a list of Sends starts one copy
    of `convert_file` per element, each with its own job, all at once.

    Each job's companion list is read off disk *now*, at dispatch time, which is
    the point of waves: everything this file imports that could be converted
    already has been, and is sitting in the output tree.
    """
    waves = state.get("waves", [])
    number = state.get("wave", 0)
    if not 1 <= number <= len(waves):
        return "finish"
    root, out_root = Path(state["root"]), Path(state["out_root"])
    by_path = {f.path: f for f in state["manifest"].files}
    jobs = []
    for path in waves[number - 1]:
        companions = [out_root / dep for dep in by_path[path].imports]
        jobs.append(Send("convert_file", FileJob(
            path=path, wave=number, source_path=str(root / path),
            output_path=str(out_root / path),
            # A companion that is missing is a dependency that failed to convert.
            # Sending a path that is not there would crash intake; leaving it out
            # converts this file without it, which 9.3's report will say out loud.
            context_paths=[str(p) for p in companions if p.exists()],
        )))
    return jobs


def convert_file(job: FileJob, runtime: Runtime[SuiteSettings] | None = None, *,
                 store: Optional[BaseStore] = None) -> SuiteState:  # noqa: UP045
    """One file, start to finish: the single-file graph invoked as a subgraph.

    Everything Phase 0-8 built happens in here — classify or refuse, draft,
    four gates, critic, up to three laps — and this node's whole job is to hand
    it one file's worth of inputs, write what came back, and reduce the report
    to one row of the suite's table.

    The converted file is written even when it needs review, on purpose: a
    file with open findings is still the best available version, and the wave
    after this one imports it. Only a run that produced no code at all writes
    nothing.
    """
    run = settings(runtime)
    started = time.time()
    child = single.build_graph(store=store)
    child_run = single.RunSettings(model=run.model, critic_model=run.critic_model,
                                   max_attempts=run.max_attempts)
    inputs = {"source_path": job["source_path"], "output_path": job["output_path"],
              "context_paths": list(job.get("context_paths", [])), "ask_risks": False}
    if run.user_id:
        inputs["user_id"] = run.user_id
    try:
        final = child.invoke(inputs, config={"run_name": f"convert:{job['path']}",
                                             "tags": [f"file:{job['path']}", f"wave:{job['wave']}"],
                                             "recursion_limit": 3 * MAX_ATTEMPTS + 5},
                             context=child_run)
    except Exception as exc:  # a crash in one file must not take the suite down
        return {"outcomes": [FileOutcome(
            path=job["path"], wave=job["wave"], status="failed", attempts=0,
            reason=f"the conversion graph raised: {exc or type(exc).__name__}",
            seconds=time.time() - started, errors=(str(exc) or type(exc).__name__,))]}
    return {"outcomes": [record(job, final, time.time() - started)]}


def record(job: FileJob, final: dict, seconds: float) -> FileOutcome:
    """One finished child run, flattened into one row. No I/O beyond the write."""
    if final.get("status") == "refused":
        return FileOutcome(path=job["path"], wave=job["wave"], status="refused", attempts=0,
                           reason=final.get("refusal", ""), seconds=seconds)
    report: ConversionReport | None = final.get("report")
    if report is None or report.result is None:
        return FileOutcome(
            path=job["path"], wave=job["wave"], status="failed",
            attempts=final.get("iteration", 0),
            reason=report.reason if report else "the graph produced no report",
            seconds=seconds, usage=final.get("usage"), critic_usage=final.get("critic_usage"),
            errors=tuple(report.errors) if report else ())
    target = Path(job["output_path"])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(report.result.code, encoding="utf-8")
    passed = {r.gate: r.passed for r in report.validation}
    return FileOutcome(
        path=job["path"], wave=job["wave"], status=report.status, attempts=report.attempts,
        reason=report.reason, gates=tuple((g, passed[g]) for g in GATES if g in passed),
        critic=report.critique.verdict if report.critique is not None else "",
        todos=tuple(report.result.todos), written=str(target), seconds=seconds,
        usage=final.get("usage"), critic_usage=final.get("critic_usage"),
        errors=tuple(report.errors))


def finish(state: SuiteState) -> SuiteState:
    """Nothing left to dispatch. Stop the clock; 9.3 will do the assembling here."""
    return {"elapsed": time.time() - state.get("started", time.time())}


def build_suite_graph(store: BaseStore | None = None):
    """plan → (next_wave → fan out a wave)* → finish.

    `add_conditional_edges("next_wave", dispatch, ["convert_file", "finish"])` —
    the third argument is the list of nodes the edge may reach, and it exists
    for the drawing, not the routing: Sends carry their own destination.

    `input_schema=FileJob` on the node is the other half of the Send contract.
    It says this node is entered with a job, not with the suite's state, which
    is what makes `convert_file(job)` honest about what it can see.
    """
    builder = StateGraph(SuiteState, context_schema=SuiteSettings)
    builder.add_node("plan", plan)
    builder.add_node("next_wave", next_wave)
    builder.add_node("convert_file", convert_file, input_schema=FileJob)
    builder.add_node("finish", finish)
    builder.add_edge(START, "plan")
    builder.add_edge("plan", "next_wave")
    builder.add_conditional_edges("next_wave", dispatch, ["convert_file", "finish"])
    # Every branch of the fan-out comes back here, and next_wave runs once, not
    # once per branch: that is the join, and it is why wave N+1 cannot start
    # early.
    builder.add_edge("convert_file", "next_wave")
    builder.add_edge("finish", END)
    return builder.compile(store=store)


def ordered(state: dict) -> list[FileOutcome]:
    """The outcomes in plan order — they arrive in whatever order they finished."""
    return sorted(state.get("outcomes", []), key=lambda o: (o.wave, o.path))


def totals(outcomes: list[FileOutcome]) -> dict[str, int]:
    """How many of each status, for the summary line and the exit code."""
    counts = {"passed": 0, "needs-review": 0, "refused": 0, "failed": 0}
    for outcome in outcomes:
        counts[outcome.status] = counts.get(outcome.status, 0) + 1
    return counts


def suite_usage(outcomes: list[FileOutcome], role: str = "usage") -> dict | None:
    """Every file's token counts added up; None when nothing was counted."""
    total = None
    for outcome in outcomes:
        total = sum_usage(total, getattr(outcome, role))
    return total


def run_json(state: dict, exit_code: int) -> dict:
    """The whole suite run as plain data — the machine-readable artifact of 9.2."""
    outcomes = ordered(state)
    manifest: suite.Manifest = state["manifest"]
    return {
        "schema": RUN_SCHEMA,
        "root": manifest.root,
        "out_root": state.get("out_root", ""),
        "exit_code": exit_code,
        "seconds": round(state.get("elapsed", 0.0), 2),
        "waves": [list(wave) for wave in state.get("waves", [])],
        "copied": list(state.get("copied", [])),
        "skipped": [{"path": f.path, "reason": f.reason}
                    for f in manifest.files if f.action == suite.SKIP],
        "counts": totals(outcomes),
        "usage": {"conversion": suite_usage(outcomes, "usage"),
                  "critic": suite_usage(outcomes, "critic_usage")},
        "files": [{
            "path": o.path, "wave": o.wave, "status": o.status, "attempts": o.attempts,
            "reason": o.reason, "gates": {gate: ok for gate, ok in o.gates},
            "critic": o.critic, "todos": list(o.todos), "written": o.written,
            "seconds": round(o.seconds, 2), "errors": list(o.errors),
        } for o in outcomes],
    }
