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

The per-file outcomes this module collects are the raw material for step 9.3:
`finish` hands them to `assemble.py`, which compiles the finished tree as one
project, adds the results up, works out what public API survived, and writes the
report. That is the last node, and it is where a folder full of files becomes
something you can hand to somebody.
"""

from __future__ import annotations

import operator
import shutil
import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Annotated, Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.store.base import BaseStore
from langgraph.types import Send

from selenium2playwright import assemble
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
    # What the model said it decided. Kept because 9.3's parity ledger looks
    # here for the reason a public member disappeared, and quotes it verbatim.
    notes: tuple[str, ...] = ()
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
    carried_paths: list[str]  # every path in this job that is the folder's untouched source
    # The rest of the suite, for the prompt and nothing else — see `repo_evidence`.
    repo_paths: list[str]
    caller_paths: list[str]
    pending_paths: list[str]
    via: str  # the scanner's "reaches Selenium through …", or "" (see graph.ConversionState)


class SuiteState(TypedDict, total=False):
    """Everything the suite graph knows. `outcomes` is the only reduced key."""

    # inputs
    root: str
    out_root: str
    # The other shape of the same input, for a caller with no filesystem here:
    # relative path -> text. `plan` writes it into a disposable directory and
    # sets root/out_root to point at that, so every node after this one is the
    # step 9.2 and 9.3 code unchanged — it still walks a folder and still
    # compiles a real tree. See `suite.materialize`.
    source_tree: dict[str, str]
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
    # where the markdown report goes; empty means out_root/conversion-report.md
    report_path: str
    # filled by finish (step 9.3)
    elapsed: float
    assembly: assemble.Assembly
    # The converted tree as text, filled only for a `source_tree` run: a caller
    # who sent bytes has no way to read the folder we wrote, so the folder has
    # to come back the same way it went in. Empty for a `root` run, where the
    # files are already on the caller's own disk and copying them into the
    # response would be sending somebody their own filesystem.
    converted_tree: dict[str, str]
    # The temp directory a `source_tree` run built. Deleted by `finish`; kept in
    # state only so `finish` knows there is something to delete.
    workspace: str


# How long a workspace may sit before it is considered abandoned. Longer than
# any real suite run and shorter than a deployment's uptime, which is the whole
# window this needs to cover.
WORKSPACE_TTL = 3600


def sweep_workspaces(ttl: int = WORKSPACE_TTL) -> int:
    """Delete workspaces from runs that never reached `finish`. Returns the count.

    `finish` deletes its own, and on the happy path that is the end of it. But a
    run that raises between `plan` and `finish` — a provider outage, a
    recursion limit, a cancelled request — never reaches `finish` at all, and on
    a long-lived public host every one of those leaves a copy of somebody's
    suite on disk until the machine is recycled.

    A sweep at the start of the next run rather than a background task or an
    `atexit` hook: it needs no scheduler, it cannot outlive the process that
    registered it, and the moment a new suite starts is exactly when an old one
    is provably finished. Failures are ignored on purpose — another worker
    sweeping the same directory at the same time is the expected case, not an
    error, and a tidy-up that could fail a conversion would be worse than the
    mess.
    """
    cutoff, swept = time.time() - ttl, 0
    try:
        candidates = list(Path(tempfile.gettempdir()).glob("s2p-suite-*"))
    except OSError:
        return 0
    for path in candidates:
        try:
            if path.is_dir() and path.stat().st_mtime < cutoff:
                shutil.rmtree(path, ignore_errors=True)
                swept += 1
        except OSError:
            continue
    return swept


def plan(state: SuiteState) -> SuiteState:
    """Scan the folder, carry the support files across, and settle the wave list.

    No model runs here. The support files are copied now rather than at the end
    because they are *context*: `tests/login.spec.ts` imports `../support/users`,
    and the converted file has to compile against something.
    """
    made: dict[str, str] = {}
    if state.get("source_tree") and not state.get("root"):
        sweep_workspaces()
        # A text run. The workspace is created here rather than by the caller
        # because the caller is a browser: it has bytes and no path, and the
        # one thing it must never get to do is choose where on this machine
        # they land. `materialize` refuses the whole tree if any key is not a
        # plain relative path.
        workspace = Path(tempfile.mkdtemp(prefix="s2p-suite-"))
        suite.materialize(state["source_tree"], workspace / "src")
        made = {"root": str(workspace / "src"), "out_root": str(workspace / "out"),
                "workspace": str(workspace)}

    root = Path(made.get("root") or state["root"])
    out_root = Path(made.get("out_root") or state["out_root"])
    manifest = state.get("manifest") or suite.scan(root)
    patterns = list(state.get("only", []))

    copied = []
    for item in manifest.files:
        if item.action == suite.COPY:
            target = out_root / item.path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / item.path, target)
            copied.append(item.path)
    # The fixtures travel with them. Nothing converts a JSON file, but a suite
    # that reads `tests/testdata/login.json` needs it in the output tree twice
    # over: the converted spec imports it, so the compile gate cannot resolve
    # that import without it, and the tree a visitor downloads has to run.
    for asset in manifest.assets:
        target = out_root / asset
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / asset, target)
        copied.append(asset)

    waves = [[p for p in wave if selected(p, patterns)] for wave in manifest.waves]
    return {"manifest": manifest, "waves": [w for w in waves if w], "copied": copied,
            "wave": 0, "started": time.time(), **made}


# `selected` lives in `suite` now, next to `conversions`, so the guard can apply
# `--only` without importing the graph. Re-exported so nothing that says
# `suite_graph.selected` has to change.
selected = suite.selected


def next_wave(state: SuiteState) -> SuiteState:
    """The loop counter, and the join point every fanned-out branch returns to."""
    return {"wave": state.get("wave", 0) + 1}


def needed_by(path: str, by_path: dict) -> list[str]:
    """Every in-suite file this one needs to compile — imports of imports included.

    `SuiteFile.imports` is what a file imports *directly*, and handing only that
    to the compile gate is wrong for any suite more than two deep. A spec
    imports its page object; the page object extends a BasePage; the spec's gate
    is then given the page object alone, `../pages/BasePage` does not resolve,
    and the file fails compile for a reason that has nothing to do with its
    conversion. Worse than a wrong verdict: the repair loop reads that finding
    and spends all three attempts rewriting code that was already correct.

    It stayed hidden because `samples/selenium-suite` is exactly two deep, where
    direct and transitive are the same list. `samples/selenium-hard-suite` is
    three, and shows it immediately.

    Breadth-first, and it tolerates a cycle rather than dying on one: TypeScript
    allows circular imports, so a suite containing one is a suite this must
    still convert. Order is deterministic — the walk order, not a set — because
    a companion list that reshuffles between runs would make two identical
    conversions produce two different prompts.
    """
    seen: set[str] = set()
    ordered: list[str] = []
    queue = list(by_path[path].imports) if path in by_path else []
    while queue:
        dep = queue.pop(0)
        if dep in seen or dep == path or dep not in by_path:
            continue
        seen.add(dep)
        ordered.append(dep)
        queue.extend(by_path[dep].imports)
    return ordered


def data_needed_by(path: str, by_path: dict) -> list[str]:
    """The JSON fixtures this file's compile needs — its own and its imports'.

    Transitive for the same reason `needed_by` is: a spec may never name
    `login.json` itself and still fail to compile without it, because the page
    object it imports reads it.
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for owner in [path, *needed_by(path, by_path)]:
        for asset in getattr(by_path.get(owner), "data_imports", ()) or ():
            if asset not in seen:
                seen.add(asset)
                ordered.append(asset)
    return ordered


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
        deps = needed_by(path, by_path)
        # Fixtures ride along as companions but never as `carried`: a JSON file
        # is not unconverted Selenium, it is data, and the prompt says so.
        companions = [out_root / dep for dep in deps]
        companions += [out_root / asset for asset in data_needed_by(path, by_path)]
        # Not everything in the output tree is a conversion. A helper with no
        # automation in it is copied across untouched — true of anything the
        # manifest did not mark `convert` — so the path this reads holds the
        # folder's original source. It still goes in `context_paths`, because
        # `tsc` cannot resolve an import to a file it was not given; it goes in
        # `carried_paths` too, so the prompt says which is which instead of
        # introducing raw Selenium as the API to match.
        carried = {str(out_root / dep) for dep in deps
                   if getattr(by_path.get(dep), "action", suite.CONVERT) != suite.CONVERT}
        # A companion that is missing is a dependency that failed to convert.
        # Sending a path that is not there would crash intake; leaving it out
        # converts this file without it, which 9.3's report will say out loud.
        present = [p for p in companions if p.exists()]
        evidence = repo_evidence(path, by_path, present, root, out_root)
        jobs.append(Send("convert_file", FileJob(
            path=path, wave=number, source_path=str(root / path),
            output_path=str(out_root / path),
            context_paths=[str(p) for p in present],
            carried_paths=[str(p) for p in present if str(p) in carried] + evidence.pop("carried"),
            # The scanner's verdict travels with the job. Without it the child
            # graph classifies the file alone and refuses a wrapper repo's page
            # objects — which it did, nine of thirteen, on the first live run.
            via=getattr(getattr(by_path.get(path), "classification", None), "via", "") or "",
            **evidence,
        )))
    return jobs


def repo_evidence(path: str, by_path: dict, companions: list[Path],
                  root: Path, out_root: Path) -> dict[str, list[str]]:
    """Every other file the output tree will hold, as reading material for one job.

    The companions are what the target imports, and the compile gate needs
    them. This is everything else, and no gate ever sees it: the files that
    import the target, so a page object can keep the member names its callers
    use (T13); the siblings converted earlier in this run, so the tenth page
    object is converted the way the first nine were; and the rest of the tree,
    for the base classes and conventions a single file cannot show.

    Each file is read from the output tree when it is there — converted in an
    earlier wave, or copied across — and from the source tree when it is not,
    which means it is still to be converted (`pending`). A file the plan skips
    is not in either tree and is not here. Order is relevance, because the
    prompt's byte budget keeps a prefix: direct callers first, then converted
    siblings, then the files still to be converted, then the copied helpers,
    each group by path. A companion that failed to convert is *not* excluded —
    it is missing from `context_paths` for that reason — so the target can at
    least see its original.
    """
    action = {p: getattr(f, "action", suite.CONVERT) for p, f in by_path.items()}
    shown = {p.resolve() for p in companions}
    others = [p for p in by_path if p != path and action[p] != suite.SKIP
              and (out_root / p).resolve() not in shown and (root / p).resolve() not in shown]
    callers = [p for p in others if p in set(getattr(by_path[path], "imported_by", ()) or ())]
    converted = [p for p in others if action[p] == suite.CONVERT and (out_root / p).exists()]
    rest = [p for p in others if p not in callers and p not in converted]
    ordered = (callers + [p for p in converted if p not in callers]
               + [p for p in rest if action[p] == suite.CONVERT]
               + [p for p in rest if action[p] != suite.CONVERT])

    def where(p: str) -> str:
        return str(out_root / p if (out_root / p).exists() else root / p)

    return {
        "repo_paths": [where(p) for p in ordered],
        "caller_paths": [where(p) for p in callers],
        "pending_paths": [where(p) for p in ordered if action[p] == suite.CONVERT
                          and not (out_root / p).exists()],
        "carried": [where(p) for p in ordered if action[p] == suite.COPY],
    }


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
              "context_paths": list(job.get("context_paths", [])),
              "carried_paths": list(job.get("carried_paths", [])),
              "repo_paths": list(job.get("repo_paths", [])),
              "caller_paths": list(job.get("caller_paths", [])),
              "pending_paths": list(job.get("pending_paths", [])),
              "via": job.get("via") or "",
              "ask_risks": False}
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
        todos=tuple(report.result.todos), notes=tuple(report.result.notes),
        written=str(target), seconds=seconds,
        usage=final.get("usage"), critic_usage=final.get("critic_usage"),
        errors=tuple(report.errors))


def finish(state: SuiteState, runtime: Runtime[SuiteSettings] | None = None) -> SuiteState:
    """Nothing left to dispatch — so ask what the whole tree adds up to (step 9.3).

    The four assembled facts live here, inside the graph, rather than in the CLI
    afterwards: the graph produced the tree, so the graph is what says whether
    the tree holds together, and the whole-tree compile shows up in the trace
    beside the conversions that made it necessary.
    """
    outcomes = ordered(state)
    elapsed = time.time() - state.get("started", time.time())
    root, out_root = Path(state["root"]), Path(state["out_root"])
    manifest = state.get("manifest")
    run = settings(runtime)
    built = assemble.assemble(root, out_root, manifest, outcomes)
    markdown = assemble.render(root, out_root, manifest, outcomes, built, elapsed=elapsed,
                               models={"actor": run.model, "critic": run.critic_model},
                               usage=suite_usage(outcomes))
    report = Path(state.get("report_path") or out_root / assemble.REPORT_NAME)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(markdown, encoding="utf-8")

    # A text run has to be handed its files back, and then the workspace has to
    # go. Read first, delete second, and delete in a `finally` — a run that
    # raised while assembling would otherwise leave a copy of somebody's suite
    # on a public host until the machine was recycled.
    workspace = state.get("workspace") or ""
    converted: dict[str, str] = {}
    if workspace:
        try:
            converted = assemble.read_tree(out_root)
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    return {"elapsed": elapsed,
            "converted_tree": converted,
            # The report path is a real file for a `root` run and a deleted temp
            # path for a text one; saying nothing is better than pointing at
            # something that is not there.
            "assembly": replace(built, markdown=markdown,
                                report_path="" if workspace else str(report))}


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
            "critic": o.critic, "todos": list(o.todos), "notes": list(o.notes),
            "written": o.written,
            "seconds": round(o.seconds, 2), "errors": list(o.errors),
        } for o in outcomes],
    }
