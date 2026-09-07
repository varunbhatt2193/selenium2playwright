"""Step 5.2 — a bounded convert/validate/critic loop, followed by assembly.

    uv run python -m selenium2playwright.graph samples/selenium-suite/pages/LoginPage.ts
    uv run python -m selenium2playwright.graph some/webdriverio.e2e.ts   # -> clean refusal

    # step 7.1 — two turns of one conversation, the file named only once
    uv run python -m selenium2playwright.graph --thread login LoginPage.ts --out out/LoginPage.ts
    uv run python -m selenium2playwright.graph --thread login --refine "use data-testid locators"

Same work as one_shot.py, restructured as a graph so that (a) every step is a
named node in the LangSmith trace and (b) the next steps — classify/refuse,
validate, critic loop — are new nodes and edges, not a rewrite.

Vocabulary used here, from the LangGraph docs:
  state  — one dict that flows through the graph; every node reads it and
           returns the *part* it changed (a partial dict). LangGraph merges.
  node   — a plain Python function: state in, partial state out.
  edge   — "after node A, run node B". START and END are the built-in ends.
  conditional edge — "after node A, call this function; it returns the NAME
           of the next node". The graph branches on data, not the model.
  compile() — turns the wiring into a Runnable (invoke/stream/batch like a chain).
  checkpointer — an object compile() writes the state to after every super-step,
           filed under config["configurable"]["thread_id"]. Invoking the same
           thread again loads that state first, so the graph starts a turn
           already knowing what the last turn produced (see memory.py).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path
from typing import Literal, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from selenium2playwright import memory
from selenium2playwright.classify import Classification, classify
from selenium2playwright.llm import make_model, prepare_messages
from selenium2playwright.one_shot import report_ledger, report_usage
from selenium2playwright.prompts import (build_critic_prompt, build_prompt, format_context,
                                         format_conventions)
from selenium2playwright.reflection import (MAX_ATTEMPTS, collect_todos, refinement_feedback,
                                            resolve_attempt_cap, revision_feedback, sum_usage)
from selenium2playwright.schemas import ConversionReport, ConversionResult, Critique, Finding, ValidationReport
from selenium2playwright.validators.compile import compile_check
from selenium2playwright.validators.lint import lint_check
from selenium2playwright.validators.parity import parity_check
from selenium2playwright.validators.residue import residue_check


class ConversionState(TypedDict, total=False):
    """Everything the graph knows about one conversion. Nodes fill it in.

    total=False: keys are optional, because the caller supplies only the
    inputs (source_path, context_paths) and later nodes add the rest.
    """

    # inputs — set by the caller
    source_path: str
    context_paths: list[str]
    output_path: str  # optional intended output location; anchors companion imports
    max_attempts: int  # optional lap budget, 1..MAX_ATTEMPTS; intake fills in the default (step 6.3)
    refinement: str  # this turn's new instruction from the user; "" on a first turn (step 7.1)
    # short-term memory (step 7.1) — written by intake, restored by the checkpointer
    turn: int  # how many times this thread has been invoked; 1 on a fresh thread
    conventions: list[str]  # every instruction this thread has been given, oldest first
    baseline: ConversionResult | None  # the previous turn's accepted output, this turn's start
    # filled by intake
    source: str  # the Selenium file contents
    context: str  # already-converted companions, formatted for the prompt ("" if none)
    context_files: dict[str, str]  # absolute companion path -> contents captured at intake
    classification: Classification  # what the file is, and whether we can convert it
    # filled by convert OR refuse — exactly one of them runs
    status: Literal["converted", "refused", "failed"]
    result: ConversionResult | None
    usage: dict | None  # cumulative conversion token/cache counts for this run
    refusal: str  # the honest reason, when status == "refused"
    # filled by validate; conversion status and validation verdict are separate
    validation: list[ValidationReport]
    # filled by critic; an unavailable review is explicit, never an automatic pass
    critique: Critique | None
    critic_usage: dict | None
    critique_error: str
    iteration: int  # conversion attempts started; 1-based after the first call
    conversion_error: str
    report: ConversionReport | None


def intake(state: ConversionState) -> ConversionState:
    """Read the files off disk, then open a turn. No LLM.

    On a fresh thread every memory key starts empty. On a thread the
    checkpointer has restored, `state` already holds the previous turn's report
    and standing instructions, so this is where one turn hands over to the next:
    the previous result becomes this turn's `baseline`, a new `refinement`
    joins `conventions`, and everything belonging to the finished turn — draft,
    validation, review, attempt counter — is cleared so the turn starts honest.
    The source is re-read rather than restored: the file on disk is the truth.
    """
    source = Path(state["source_path"]).read_text(encoding="utf-8")
    paths = [Path(p) for p in state.get("context_paths", [])]
    context_files = {str(p.resolve()): p.read_text(encoding="utf-8") for p in paths}
    context = format_context(paths, contents=context_files)
    previous = state.get("report")
    conventions = list(state.get("conventions", []))
    refinement = (state.get("refinement") or "").strip()
    if refinement and refinement not in conventions:
        conventions.append(refinement)
    return {"source": source, "context": context, "context_files": context_files,
            "classification": classify(source, state["source_path"]),
            "max_attempts": resolve_attempt_cap(state.get("max_attempts")),
            "turn": state.get("turn", 0) + 1, "conventions": conventions, "refinement": "",
            "baseline": previous.result if previous is not None else None,
            "iteration": 0, "result": None, "validation": [], "critique": None,
            "conversion_error": "", "critique_error": "", "usage": None,
            "critic_usage": None, "report": None}


def route_after_intake(state: ConversionState) -> Literal["convert", "refuse"]:
    """The branching decision. Reads state, returns the next node's name."""
    return "convert" if state["classification"].supported else "refuse"


def refuse(state: ConversionState) -> ConversionState:
    """Honest 'not supported': no model call, no half-converted file."""
    return {"status": "refused", "refusal": state["classification"].reason}


def convert(state: ConversionState) -> ConversionState:
    """Draft, repair, or refine — the trailing message says which, the node is one.

    Three ways in: a first draft (no feedback), a repair of this turn's draft
    (revision_feedback: the draft plus its findings and critic fixes), or the
    first attempt of a later turn on the same thread (refinement_feedback: the
    previous turn's accepted file). Standing instructions ride along on every
    one of them, so a repair lap can never quietly undo the user's convention.
    """
    iteration = state.get("iteration", 0) + 1
    usage = None
    try:
        feedback = ""
        if state.get("result") is not None and state.get("critique") is not None:
            feedback = revision_feedback(state["result"], state["validation"], state["critique"])
        elif state.get("baseline") is not None:
            feedback = refinement_feedback(state["baseline"])
        structured_model = make_model().with_structured_output(ConversionResult, include_raw=True)
        chain = (build_prompt(revision=feedback, conventions=format_conventions(state.get("conventions", [])))
                 | prepare_messages() | structured_model)
        response = chain.invoke(
            {"file_path": state["source_path"], "source": state["source"], "context": state["context"]}
        )
        usage = response["raw"].usage_metadata
        if response["parsing_error"] is not None:
            raise RuntimeError(f"model reply did not match ConversionResult: {response['parsing_error']}")
        result = ConversionResult.model_validate(response["parsed"])
        if not result.code.strip():
            raise ValueError("model returned an empty converted file")
    except Exception as exc:
        # On a failed repair, keep the previous code and its validation/review.
        return {"status": "converted" if state.get("result") is not None else "failed",
                "conversion_error": str(exc) or type(exc).__name__, "iteration": iteration,
                "usage": sum_usage(state.get("usage"), usage)}
    return {"status": "converted", "result": result, "conversion_error": "",
            "iteration": iteration, "usage": sum_usage(state.get("usage"), usage)}


def route_after_convert(state: ConversionState) -> Literal["validate", "assemble"]:
    """An unavailable conversion still gets a final report, retaining any prior draft."""
    return "assemble" if state.get("conversion_error") else "validate"


def validate(state: ConversionState) -> ConversionState:
    """Run every gate, retaining failures for the scorecard and critic."""
    target = Path(state.get("output_path", state["source_path"])).resolve()
    companions = {Path(p): code for p, code in state.get("context_files", {}).items()}
    if target in companions:
        raise ValueError("output_path must differ from the companion files")
    # Preserve the intended output tree: tests/X.ts can import ../pages/Y.ts.
    # Absolute disk paths never become sandbox file keys, and no import is guessed.
    base = Path(os.path.commonpath([str(p.parent) for p in [target, *companions]]))
    relative = target.relative_to(base).as_posix()
    converted = {relative: state["result"].code}
    files = {p.relative_to(base).as_posix(): code for p, code in companions.items()} | converted
    checks = [
        ("compile", lambda: compile_check(files)),
        ("residue", lambda: residue_check(files)),
        ("lint", lambda: lint_check(files)),
        # Companions are already converted; only the current file has a source pair.
        ("parity", lambda: parity_check({relative: state["source"]}, converted)),
    ]
    reports = []
    for gate, check in checks:
        try:
            reports.append(check())
        except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as exc:
            # A missing tool or timeout fails its layer, while other gates still run.
            reports.append(ValidationReport(gate=gate, passed=False, findings=[
                Finding(gate=gate, file=relative, code="validator-error", message=str(exc)),
            ], tool_output=str(exc)))
    return {"validation": reports}


def report_validation(reports: list[ValidationReport]) -> bool:
    """Scorecard and findings go to stderr, leaving stdout as usable TypeScript."""
    print("Validation (report-only):", file=sys.stderr)
    if not reports:
        print("  NOT RUN — no converted file available", file=sys.stderr)
        return False
    for report in reports:
        print(f"  {'PASS' if report.passed else 'FAIL'} {report.gate}: {len(report.findings)} finding(s)",
              file=sys.stderr)
        for finding in report.findings:
            print(f"    {finding.render()}", file=sys.stderr)
        if not report.passed and not report.findings and report.tool_output:
            print(report.tool_output, file=sys.stderr)
    return all(report.passed for report in reports)


def critic(state: ConversionState) -> ConversionState:
    """Review this draft once; the conditional edge decides whether to repair it."""
    usage = None
    try:
        structured_model = make_model(for_critic=True).with_structured_output(
            Critique, method="json_schema", include_raw=True,
        )
        chain = (build_critic_prompt(conventions=format_conventions(state.get("conventions", [])))
                 | prepare_messages(for_critic=True) | structured_model)
        evidence = "\n\n".join(
            f"{'PASS' if r.passed else 'FAIL'} {r.render()}"
            + (f"\n{r.tool_output}" if not r.passed and not r.findings else "")
            for r in state["validation"]
        )
        response = chain.invoke({
            "file_path": state["source_path"], "source": state["source"],
            "context": state.get("context", ""),
            "conversion": state["result"].model_dump_json(indent=2), "validation": evidence,
        })
        usage = response["raw"].usage_metadata
        if response["parsing_error"] is not None:
            raise RuntimeError(f"critic reply did not match Critique: {response['parsing_error']}")
        critique = Critique.model_validate(response["parsed"])
    except Exception as exc:
        # Provider or parsing failures must still let the CLI emit the converted
        # file and existing scorecard. Keep the review error visible and fail the run.
        return {"critique": None, "critic_usage": sum_usage(state.get("critic_usage"), usage),
                "critique_error": str(exc) or type(exc).__name__}

    failed = [report for report in state["validation"] if not report.passed]
    if failed and critique.verdict == "pass":
        # The model cannot override a deterministic failure. These fallback fixes
        # come from the reports, not from a claim that the model noticed the bug.
        critique = Critique(verdict="revise", fixes=[
            f"Resolve the failed {r.gate} gate: {r.render()}"
            + (f"\n{r.tool_output}" if not r.findings else "") for r in failed
        ])
    return {"critique": critique, "critic_usage": sum_usage(state.get("critic_usage"), usage), "critique_error": ""}


def report_critique(critique: Critique | None, error: str) -> bool:
    """Print the review without mixing it into the generated TypeScript."""
    if critique is None:
        print(f"Critic: UNAVAILABLE — {error}" if error else "Critic: NOT RUN", file=sys.stderr)
        return False
    print(f"Critic: {critique.verdict.upper()}", file=sys.stderr)
    for fix in critique.fixes:
        print(f"  - {fix}", file=sys.stderr)
    return critique.verdict == "pass"


def validator_unavailable(state: ConversionState) -> bool:
    """Rewriting code cannot restore a missing or broken validation tool."""
    return any(f.code == "validator-error" for r in state["validation"] for f in r.findings)


def route_after_critic(state: ConversionState) -> Literal["convert", "assemble"]:
    """Repeat only for an actionable review while this run's attempt budget remains.

    The budget is state["max_attempts"] (default MAX_ATTEMPTS = 3). With a budget
    of 1 the critic still runs and its verdict is still reported, but no repair
    lap follows: that is the "one attempt" arm of the step 6.3 comparison.
    """
    critique = state["critique"]
    if (critique is not None and critique.verdict == "revise"
            and not state.get("critique_error") and not validator_unavailable(state)
            and state["iteration"] < state.get("max_attempts", MAX_ATTEMPTS)):
        return "convert"
    return "assemble"


def assemble(state: ConversionState) -> ConversionState:
    """Package the latest available code, final scorecard, review, and TODO ledger."""
    result = collect_todos(state["result"]) if state.get("result") is not None else None
    reports = state.get("validation", [])
    critique = state.get("critique")
    errors = [error for error in (state.get("conversion_error"), state.get("critique_error")) if error]
    status = "needs-review"
    if state.get("conversion_error"):
        reason = "Conversion failed; retaining the last available draft, if any."
    elif state.get("critique_error") or critique is None:
        reason = "The critic was unavailable; the code has not received a complete review."
    elif validator_unavailable(state):
        reason = "A validation tool failed; restore it before requesting another conversion."
    elif len(reports) != 4 or not all(r.passed for r in reports) or critique.verdict != "pass":
        reason = (f"Stopped after {state['iteration']} of {state.get('max_attempts', MAX_ATTEMPTS)} "
                  "allowed attempts; findings remain.")
    elif result is None:
        reason = "No converted file is available."
    elif result.todos:
        reason = "Validation and the critic passed, but TODO(review) items still need a human."
    else:
        status, reason = "passed", "All four gates and the critic passed; no open TODO(review) items."
    report = ConversionReport(status=status, attempts=state["iteration"], reason=reason,
                              result=result, validation=reports, critique=critique, errors=errors)
    return {"report": report}


def build_graph(checkpointer: BaseCheckpointSaver | None = None):
    """Up to max_attempts convert/validate/critic laps, then assemble. Refuse goes to END.

    checkpointer=None (the default) is the stateless graph every earlier phase
    and the eval runner use: nothing is written, nothing is restored. Pass one
    and each invoke becomes a turn of the conversation named by thread_id.
    """
    builder = StateGraph(ConversionState)
    builder.add_node("intake", intake)
    builder.add_node("convert", convert)
    builder.add_node("refuse", refuse)
    builder.add_node("validate", validate)
    builder.add_node("critic", critic)
    builder.add_node("assemble", assemble)
    builder.add_edge(START, "intake")
    # After intake, ask route_after_intake which node comes next. The mapping
    # {returned name: node name} is what lets LangGraph draw the branch.
    builder.add_conditional_edges("intake", route_after_intake,
                                  {"convert": "convert", "refuse": "refuse"})
    builder.add_conditional_edges("convert", route_after_convert,
                                  {"validate": "validate", "assemble": "assemble"})
    builder.add_edge("validate", "critic")
    builder.add_conditional_edges("critic", route_after_critic,
                                  {"convert": "convert", "assemble": "assemble"})
    builder.add_edge("assemble", END)
    builder.add_edge("refuse", END)
    return builder.compile(checkpointer=checkpointer)


def report_thread(state: ConversionState, db: Path, thread_id: str) -> None:
    """What this conversation remembers, so a resumed turn is never a black box."""
    print(f"Thread {thread_id!r} · turn {state.get('turn', 1)} · {db}", file=sys.stderr)
    if state.get("baseline") is not None:
        print("  continuing from the previous turn's conversion", file=sys.stderr)
    for number, convention in enumerate(state.get("conventions", []), 1):
        print(f"  standing instruction {number}: {convention}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Convert, validate, and repair Selenium TypeScript in up to three attempts")
    parser.add_argument("source", nargs="?", type=Path,
                        help="the Selenium file; omit it to continue a saved --thread")
    parser.add_argument("context", nargs="*", type=Path, help="already-converted companion files")
    parser.add_argument("--out", type=Path, help="output file; also anchors relative imports to companions")
    parser.add_argument("--max-attempts", type=int, default=MAX_ATTEMPTS, choices=range(1, MAX_ATTEMPTS + 1),
                        help="total conversion attempts: 1 = no repairs; 3 = initial draft plus two repairs")
    parser.add_argument("--thread", help="conversation id; saves this turn and resumes the last one (step 7.1)")
    parser.add_argument("--refine", default="", metavar="INSTRUCTION",
                        help='a standing instruction for this thread, e.g. "use data-testid locators"')
    parser.add_argument("--db", type=Path, default=memory.DEFAULT_DB, help="thread database file")
    parser.add_argument("--list-threads", action="store_true", help="print saved thread ids in --db and exit")
    args = parser.parse_args(argv)
    if args.list_threads:
        for thread_id in memory.list_threads(args.db):
            print(thread_id)
        return 0
    if args.source is None and not args.thread:
        parser.error("give a source file, or --thread <id> to continue a saved conversation")
    if args.out and args.source is not None and args.out.resolve() in {p.resolve() for p in
                                                                      [args.source, *args.context]}:
        parser.error("--out must differ from the source and companion files")

    # Only keys the caller actually supplied: anything omitted on a later turn
    # keeps the value the checkpointer restored, which is the whole point.
    inputs: ConversionState = {"max_attempts": args.max_attempts}
    if args.source is not None:
        inputs["source_path"] = str(args.source)
        inputs["context_paths"] = [str(p) for p in args.context]
    if args.out:
        inputs["output_path"] = str(args.out)
    if args.refine:
        inputs["refinement"] = args.refine
    config = {"run_name": "conversion-graph", "tags": ["step:7.1", "prompt:v1", "critic:v1"],
              "recursion_limit": 3 * MAX_ATTEMPTS + 5}

    with ExitStack() as stack:
        checkpointer = stack.enter_context(memory.open_checkpointer(args.db)) if args.thread else None
        graph = build_graph(checkpointer)
        if args.thread:
            config = memory.thread_config(args.thread, **config)
            if args.source is None and not memory.thread_state(graph, args.thread).get("source_path"):
                parser.error(f"thread {args.thread!r} has no saved conversion yet; "
                             "pass a source file to start it")
        final = graph.invoke(inputs, config=config)

    if args.thread:
        report_thread(final, args.db, args.thread)
    c = final["classification"]
    print(f"[{c.automation} · {c.runner} · {c.language}] {c.reason}", file=sys.stderr)
    if final["status"] == "refused":
        print(f"✗ not converted: {final['refusal']}", file=sys.stderr)
        return 2
    report = final["report"]
    print(f"Conversion: {report.status} ({report.attempts}/{final['max_attempts']} attempts) — {report.reason}",
          file=sys.stderr)
    for error in report.errors:
        print(f"  error: {error}", file=sys.stderr)
    if final["usage"]:
        print("Conversion token usage (all attempts):", file=sys.stderr)
        report_usage(final["usage"])
    if report.result is not None:
        report_ledger(report.result)
    report_validation(report.validation)
    report_critique(report.critique, final["critique_error"])
    if final["critic_usage"]:
        print("Critic token usage (all attempts):", file=sys.stderr)
        report_usage(final["critic_usage"])
    # A resumed turn that was not given --out still knows where the file goes.
    destination = args.out or (Path(final["output_path"]) if final.get("output_path") else None)
    if report.result is None:
        print("No converted code was produced; no output file was written.", file=sys.stderr)
        if final.get("baseline") is not None:
            # A failed refinement turn is not a lost conversion: the turn it was
            # refining is still in the thread and still on disk. Say so.
            print("The previous turn's conversion on this thread is unchanged.", file=sys.stderr)
    elif destination is not None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(report.result.code, encoding="utf-8")
        print(f"[wrote {destination}]", file=sys.stderr)
    else:
        print(report.result.code, end="")
    return 0 if report.status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
