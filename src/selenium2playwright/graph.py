"""The graph itself: a bounded convert/validate/critic loop, then assembly.

The command line that drives it lives in cli.py (step 8.1); this file is the
agent, and it has no front end of its own:

    uv run s2p convert samples/selenium-suite/pages/LoginPage.ts
    uv run s2p convert some/webdriverio.e2e.ts          # -> a clean refusal
    uv run s2p convert --thread login --refine "use data-testid locators"

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
  interrupt() — called inside a node, it stops the whole graph mid-run: the
           state so far is already saved, and invoke() returns just the question.
           The run continues only when someone invokes the same thread with
           Command(resume=answer). The node is then re-run from its first line
           and that interrupt() call returns the answer (see risk_review).
  context — the run-scoped settings of a single invoke: not what the run is
           about (that is state) but *how* it runs — which model, how many
           attempts. Declared as a schema on StateGraph(context_schema=...),
           passed per call as invoke(..., context=RunSettings(...)), and handed
           to any node that declares a `runtime` parameter. Nothing about it is
           saved: a checkpointed thread restores its state, never its context,
           so a flag typed on turn 2 is obeyed on turn 2 (see RunSettings).
  store  — the other database compile() can take: not one conversation but
           everything the user has ever asked to be remembered, filed under a
           namespace instead of a thread_id. Nodes that declare a `store`
           parameter are handed it. Long-term to the checkpointer's short-term
           (see store.py).
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.store.base import BaseStore
from langgraph.types import interrupt

# Imported under another name: the node below must call its injected store
# parameter `store`, because that is the name LangGraph fills in.
from selenium2playwright import env, risk
from selenium2playwright import store as memory_store
from selenium2playwright.classify import Classification, classify
from selenium2playwright.llm import make_model, prepare_messages, structured_kwargs
from selenium2playwright.prompts import (RepoEvidence, bound, build_critic_prompt, build_prompt,
                                         format_context, format_conventions, format_decisions,
                                         format_remembered, repo_budget)
from selenium2playwright.reflection import (MAX_ATTEMPTS, collect_todos, refinement_feedback,
                                            resolve_attempt_cap, revision_feedback, sum_usage)
from selenium2playwright.schemas import ConversionReport, ConversionResult, Critique, Finding, ValidationReport
from selenium2playwright.validators.compile import compile_check
from selenium2playwright.validators.lint import lint_check
from selenium2playwright.validators.parity import parity_check
from selenium2playwright.validators.residue import residue_check


@dataclass(frozen=True)
class RunSettings:
    """Step 8.2 — the knobs one run may turn, and the graph's context schema.

    These are choices about *how* this invocation runs, which is why they are
    context and not state: state is the conversation (its source file, its
    standing instructions, its last conversion) and it is checkpointed, while
    context is passed fresh on every invoke and never saved. That distinction
    is the whole reason `s2p convert --thread login --model opus` does what it
    says on turn 2 instead of being overruled by what turn 1 recorded.

    Empty and None mean "not chosen here", so the environment (S2P_MODEL,
    S2P_CRITIC_MODEL) and then the defaults still decide — the same precedence
    llm.resolve_name has always used, now with the command line on top.
    """

    model: str = ""  # the actor, "provider:model"; "" = whatever .env says
    critic_model: str = ""  # the reviewer; "" = follow the actor (see env.resolve_roles)
    max_attempts: int | None = None  # lap budget; None = the state input, else MAX_ATTEMPTS


def settings(runtime: Runtime[RunSettings] | None) -> RunSettings:
    """This run's context, or the defaults when it was invoked without one.

    LangGraph does *not* fill in a context schema's defaults: invoke without
    `context=` and every node sees runtime.context = None, so reading a field
    off it raises AttributeError. Every node goes through here instead, which
    is also what keeps a plain build_graph().invoke({...}) — the eval runner,
    every earlier phase, most tests — working exactly as it did.
    """
    context = getattr(runtime, "context", None)
    if isinstance(context, dict):  # a caller may pass the schema's fields as a dict
        return RunSettings(**context)
    return context if isinstance(context, RunSettings) else RunSettings()


class ConversionState(TypedDict, total=False):
    """Everything the graph knows about one conversion. Nodes fill it in.

    total=False: keys are optional, because the caller supplies only the
    inputs (source_path, context_paths) and later nodes add the rest.
    """

    # inputs — set by the caller
    source_path: str  # where to read the file, OR just its name when source_text is given
    context_paths: list[str]
    # Which of those companions are *not* converted — the suite carries files
    # across untouched once past the demo's cap, and they are still Selenium.
    # They are sent anyway so imports resolve; this is what stops the prompt
    # and the critic from treating raw Selenium as the API to match.
    carried_paths: list[str]
    # 11.3b — the rest of the suite, as reading material and nothing else. A
    # suite run sends every other file in its output tree here, most relevant
    # first; `intake` reads them into `repo_files` and the prompt shows them.
    # No gate is ever handed one: `validate` reads `context_files` only, so a
    # file nobody converted cannot fail a file somebody did. `caller_paths` are
    # the ones that import the target (T13); `pending_paths` are scheduled for
    # conversion later in this run and still the original Selenium; anything
    # in `carried_paths` was copied across unconverted. All optional, all empty
    # in single-file mode, where the prompt is then byte-identical to before.
    repo_paths: list[str]
    caller_paths: list[str]
    pending_paths: list[str]
    # step 10.2 — the same two inputs, sent as text instead of as paths. A
    # deployed server has none of the caller's files, so a path is a promise it
    # cannot keep; these are how a paste box, an HTTP client or Studio hands the
    # graph a file it can actually read. Both persist on a thread exactly as
    # their path-shaped twins do, so turn 2 of a refine needs neither again.
    source_text: str  # the file's contents; "" means "read source_path from disk"
    context_text: dict[str, str]  # companion name -> contents; {} means use context_paths
    output_path: str  # optional intended output location; anchors companion imports
    max_attempts: int  # optional lap budget, 1..MAX_ATTEMPTS; intake fills in the default (step 6.3)
    refinement: str  # this turn's new instruction from the user; "" on a first turn (step 7.1)
    ask_risks: bool  # pause for the human on a flagged pattern (step 7.2); needs a checkpointer
    user_id: str  # whose long-term memories to use (step 7.3); "local" unless --user says otherwise
    remember: str  # a preference to file in long-term memory this run; "" the rest of the time
    # short-term memory (step 7.1) — written by intake, restored by the checkpointer
    turn: int  # how many times this thread has been invoked; 1 on a fresh thread
    conventions: list[str]  # every instruction this thread has been given, oldest first
    baseline: ConversionResult | None  # the previous turn's accepted output, this turn's start
    # filled by intake
    source: str  # the Selenium file contents
    context: str  # already-converted companions, formatted for the prompt ("" if none)
    context_files: dict[str, str]  # absolute companion path -> contents captured at intake
    repo_files: dict[str, str]  # the suite evidence actually shown, path -> contents; {} if none
    repo_omitted: list[tuple[str, int]]  # evidence the budget left out, with sizes
    classification: Classification  # what the file is, and whether we can convert it
    models: dict[str, str]  # the actor and critic this turn resolved to (step 8.2)
    risks: list[risk.Risk]  # patterns with more than one correct conversion (step 7.2)
    # filled by recall — long-term memory, which belongs to the user, not the thread
    recalled: list[memory_store.Memory]  # preferences close enough to this file to send
    memory_count: int  # how many the user has in total, so "none matched" can be said out loud
    # filled by risk_review — the human's answers, kept for every later turn
    decisions: dict[str, str]  # risk kind -> the answer, an option key or the user's own words
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


# A pasted file is the first input this project takes from somewhere other than
# the machine it runs on, so it is the first that needs a size of its own. The
# cap is deliberately generous — the largest file in the sample suite is under
# 4 KB — and it exists to bound one request, not to judge the code.
MAX_SOURCE_BYTES = 256 * 1024

# What to call a file nobody named. The extension is load-bearing: classify()
# and the playbook both read it, and ".ts" is the honest default for a project
# whose whole input domain is TypeScript.
PASTED_NAME = "pasted.ts"


def read_inputs(state: ConversionState) -> tuple[str, str, list[Path], dict[str, str]]:
    """This turn's file and companions — from the caller when sent, else from disk.

    Two ways in, and the difference is only where the bytes come from:

      source_path="pages/LoginPage.ts"        read it, the way every phase
                                              before 10.2 did
      source_text="import { By } ..."         use it; source_path is then only a
      source_path="LoginPage.ts"              *name*, for classify(), the recall
                                              query, the prompt and the report

    The second is what makes the graph deployable. A server in a data centre
    has none of your files, so "read this path" is a promise it cannot keep;
    everything downstream of here only ever wanted the file's name anyway,
    which is why nothing else in the graph had to change.

    Neither input is consumed. They belong to the conversation the way
    source_path always has, so a refine turn that sends nothing but a sentence
    still knows which file it is talking about — in either mode.
    """
    pasted = state.get("source_text") or ""
    if pasted:
        source, name = pasted, (state.get("source_path") or PASTED_NAME)
    else:
        # No text: the path is a real path, and it is re-read every turn rather
        # than restored, because the file on disk is the truth.
        name = state["source_path"]
        source = Path(name).read_text(encoding="utf-8")
    companions = dict(state.get("context_text") or {})
    if not companions:
        companions = {p: Path(p).read_text(encoding="utf-8")
                      for p in state.get("context_paths", [])}
    paths = [Path(p) for p in companions]
    # Keyed by resolved path in both modes: validate() and format_context() have
    # always looked companions up that way, and a bare name resolves against the
    # working directory consistently on both sides of that lookup.
    contents = {str(p.resolve()): companions[str(p)] for p in paths}
    return source, name, paths, contents


def read_repo(state: ConversionState) -> RepoEvidence | None:
    """The rest of the suite, read off disk and cut to budget; None when none was sent.

    Read here, at intake, for the same reason the companions are: the prompt
    and the trace must show the bytes this run actually saw, even if the file
    on disk changes while the run is in flight. Keyed by resolved path like
    `context_files`, so the two dictionaries can never disagree about a file.

    A budget of 0 (`S2P_REPO_CONTEXT_BYTES=0`) is "off": nothing is read and
    the prompt is what it was before the suite could send evidence at all.
    """
    paths = [Path(p) for p in state.get("repo_paths") or []]
    if not paths or repo_budget() <= 0:
        return None
    contents = {str(p.resolve()): p.read_text(encoding="utf-8", errors="replace") for p in paths}
    kept, omitted = bound(contents)
    resolved = lambda key: frozenset(str(Path(p).resolve()) for p in state.get(key) or ())  # noqa: E731
    return RepoEvidence(contents=kept, callers=resolved("caller_paths"),
                        pending=resolved("pending_paths"), carried=resolved("carried_paths"),
                        omitted=omitted)


def oversized(source: str) -> Classification | None:
    """A refusal for a file too big to be one request, or None to carry on.

    Returned as a Classification rather than raised, so an over-long paste
    leaves by the same door as a Cypress file: the refuse node, one honest
    sentence, no model call and no half-converted output.
    """
    size = len(source.encode("utf-8"))
    if size <= MAX_SOURCE_BYTES:
        return None
    return Classification(
        automation="unknown", runner="none", language="unknown", supported=False,
        # Rounded *up*, so the number the user is shown never reads as equal to
        # the limit it just exceeded.
        reason=(f"file is {-(-size // 1024)} KB; the limit for one request is "
                f"{MAX_SOURCE_BYTES // 1024} KB — convert it a file at a time"),
    )


def intake(state: ConversionState, runtime: Runtime[RunSettings] | None = None) -> ConversionState:
    """Read the files off disk, then open a turn. No LLM.

    On a fresh thread every memory key starts empty. On a thread the
    checkpointer has restored, `state` already holds the previous turn's report
    and standing instructions, so this is where one turn hands over to the next:
    the previous result becomes this turn's `baseline`, a new `refinement`
    joins `conventions`, and everything belonging to the finished turn — draft,
    validation, review, attempt counter — is cleared so the turn starts honest.
    The source is re-read rather than restored: the file on disk is the truth.

    It is also where this run's context becomes part of the record. The models
    and the lap budget are resolved once, here, and written into the state, so
    the nodes that call a model, the scorecard, the JSON report and the
    checkpoint all name the same thing. A cap given in the context wins over
    one restored from the thread, for the reason in RunSettings: the flag was
    typed now, the state is a memory of last time.

    Risks are re-detected from that same source for the same reason. Answers
    (`decisions`) are not touched: like conventions they belong to the
    conversation, so a question answered on turn 1 is not asked again on turn 2.
    """
    # runtime defaults to None so the node stays what the vocabulary above says a
    # node is — a plain function of state, callable directly in a test. LangGraph
    # injects it by parameter name either way.
    run = settings(runtime)
    cap = run.max_attempts if run.max_attempts is not None else state.get("max_attempts")
    source, name, paths, context_files = read_inputs(state)
    repo = read_repo(state)
    context = format_context(paths, contents=context_files,
                             carried=state.get("carried_paths") or (), repo=repo)
    previous = state.get("report")
    conventions = list(state.get("conventions", []))
    refinement = (state.get("refinement") or "").strip()
    if refinement and refinement not in conventions:
        conventions.append(refinement)
    return {"source": source, "context": context, "context_files": context_files,
            "repo_files": repo.contents if repo else {},
            "repo_omitted": list(repo.omitted) if repo else [],
            # source_path is written back because a paste may not have carried
            # one, and everything downstream — the scorecard title, the recall
            # query, the report — asks the state for the file's name.
            "source_path": name,
            "classification": oversized(source) or classify(source, name),
            "risks": risk.detect_risks(source),
            "max_attempts": resolve_attempt_cap(cap),
            "models": env.resolve_roles(run.model, run.critic_model),
            "turn": state.get("turn", 0) + 1, "conventions": conventions, "refinement": "",
            "baseline": previous.result if previous is not None else None,
            "recalled": [], "memory_count": 0,
            "iteration": 0, "result": None, "validation": [], "critique": None,
            "conversion_error": "", "critique_error": "", "usage": None,
            "critic_usage": None, "report": None}


def route_after_intake(state: ConversionState) -> Literal["recall", "refuse"]:
    """The branching decision. Reads state, returns the next node's name."""
    return "recall" if state["classification"].supported else "refuse"


# Optional[BaseStore], not the modern BaseStore | None, and that is not a style
# slip. LangGraph decides whether to inject the store by matching this parameter
# by name AND by the literal text of its annotation, against a fixed list that
# holds "BaseStore" and "Optional[BaseStore]" and nothing else. `from __future__
# import annotations` at the top of this file turns every annotation into a
# string, so "BaseStore | None" matches none of them — and the failure is
# silent: the node still runs, store is just always None, and long-term memory
# quietly does nothing. tests/test_store.py pins the spelling.
def recall(state: ConversionState, *, store: Optional[BaseStore] = None) -> ConversionState:  # noqa: UP045
    """Step 7.3 — file this run's new preference, then fetch the ones that fit.

    LangGraph hands a node the store by parameter name, so this signature is the
    whole wiring. store=None is the default everywhere else — the stateless
    graph, the eval runner, every earlier phase — and then this node returns
    nothing and the prompt is byte-identical to Phase 6.

    Two things happen here, in this order for a reason. A preference given now
    (--remember) is written first and always sent, because the user just said it;
    it never has to survive a similarity threshold to be obeyed this run. Then
    the store is asked which *older* preferences are close enough to this file to
    be worth the tokens — the answer is usually a subset, sometimes none, and
    both are reported rather than assumed.

    Anything already standing on this thread is excluded: a rule the user
    repeated locally should reach the model once, not twice.
    """
    if store is None:
        return {}
    user = state.get("user_id") or memory_store.DEFAULT_USER
    fresh: list[memory_store.Memory] = []
    new = (state.get("remember") or "").strip()
    if new:
        fresh.append(memory_store.remember(store, new, user, source=state["source_path"]))
    query = memory_store.recall_query(state["source_path"], state["classification"], state["source"])
    found = memory_store.recall(store, query, user,
                                exclude=[*state.get("conventions", []), *(m.text for m in fresh)])
    # "": consumed, like refinement in intake. A later turn on this thread must
    # not silently re-file a preference the user typed once.
    return {"recalled": [*fresh, *found], "remember": "",
            "memory_count": len(memory_store.memories(store, user))}


def remembered(state: ConversionState) -> str:
    """The recalled preferences as prompt text; "" when there is no store."""
    return format_remembered([m.text for m in state.get("recalled", [])])


def model_for(state: ConversionState, role: str) -> str | None:
    """The actor or critic intake resolved; None lets llm.py fall back to .env.

    The nodes that spend money read the record rather than the context, so the
    model named on the scorecard is provably the model that was called.
    """
    return state.get("models", {}).get(role) or None


def risk_review(state: ConversionState) -> ConversionState:
    """Step 7.2 — stop and ask the human about anything with two right answers.

    One interrupt() per unanswered risk. Each one suspends the graph where it
    stands; the front end (cli.convert today, a web UI later) reads the question out
    of the reply, gets an answer, and resumes the same thread with
    Command(resume=answer).

    Two properties make this safe, and both are why the node looks like this:
    LangGraph re-runs the node from the top on every resume, so it must be pure
    — no model call, no file write, nothing that must not happen twice — and it
    must ask its questions in the same order every time, because resumed answers
    are matched to interrupt() calls by position.

    ask_risks is off by default. Without it (every Phase 0-6 run and the whole
    eval suite) the risks are still detected and reported, but nothing pauses
    and nothing is added to the prompt: unanswered means the playbook decides,
    exactly as before. interrupt() also requires a checkpointer, which is why
    the CLI only turns this on for a --thread run.
    """
    if not state.get("ask_risks"):
        return {}
    answers = dict(state.get("decisions", {}))
    for flagged in state.get("risks", []):
        if flagged.kind in answers:
            continue
        answers[flagged.kind] = str(interrupt(risk.question(flagged)) or "").strip()
    return {"decisions": answers}


def guidance(state: ConversionState) -> str:
    """The human's answers as prompt text; "" when there was nothing to ask."""
    return format_decisions(risk.decision_lines(state.get("risks", []), state.get("decisions", {})))


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
        actor = model_for(state, "actor")
        structured_model = make_model(actor).with_structured_output(
            ConversionResult, **structured_kwargs(actor))
        chain = (build_prompt(revision=feedback, decisions=guidance(state),
                              conventions=format_conventions(state.get("conventions", [])),
                              remembered=remembered(state))
                 | prepare_messages(actor) | structured_model)
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
    # Which of those companions are still the folder's own Selenium. They are
    # here so `tsc` can resolve an import, not because this run converted them,
    # so an error inside one is not this file's to fix — see `compile_check`.
    still_theirs = {str(Path(p).resolve()) for p in state.get("carried_paths") or ()}
    carried = {p.relative_to(base).as_posix() for p in companions
               if str(p.resolve()) in still_theirs}
    checks = [
        # Only compile is given the companions, and only because `tsc` cannot
        # resolve an import without the file behind it. The other three ask
        # questions about *this* conversion, and a companion is not it: in a
        # suite run the companions are the folder's own untouched source, so
        # judging residue over them failed a clean Playwright file for the
        # Selenium still sitting in the file next door — twice, on a live run,
        # burning all three attempts each time.
        ("compile", lambda: compile_check(files, carried=carried)),
        ("residue", lambda: residue_check(converted)),
        ("lint", lambda: lint_check(converted)),
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


def critic(state: ConversionState) -> ConversionState:
    """Review this draft once; the conditional edge decides whether to repair it."""
    usage = None
    try:
        reviewer = model_for(state, "critic")
        # method="json_schema" only where the provider supports it: the choice
        # lives in llm.py so this node runs unchanged on any of them.
        structured_model = make_model(reviewer, for_critic=True).with_structured_output(
            Critique, **structured_kwargs(reviewer, for_critic=True),
        )
        chain = (build_critic_prompt(conventions=format_conventions(state.get("conventions", [])),
                                     decisions=guidance(state), remembered=remembered(state))
                 | prepare_messages(reviewer, for_critic=True) | structured_model)
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


def build_graph(checkpointer: BaseCheckpointSaver | None = None, store: BaseStore | None = None):
    """Up to max_attempts convert/validate/critic laps, then assemble. Refuse goes to END.

    checkpointer=None (the default) is the stateless graph every earlier phase
    and the eval runner use: nothing is written, nothing is restored. Pass one
    and each invoke becomes a turn of the conversation named by thread_id.

    store=None likewise: no long-term memory, so nothing is recalled and nothing
    is written. The two are independent — a one-off run with no thread can still
    recall what you taught it last week.

    context_schema is the third of these: it takes no argument here because it
    is not a resource to open but a shape to accept, one RunSettings per
    invoke. Passing none is still valid and still means "use .env".
    """
    builder = StateGraph(ConversionState, context_schema=RunSettings)
    builder.add_node("intake", intake)
    builder.add_node("recall", recall)
    builder.add_node("risk_review", risk_review)
    builder.add_node("convert", convert)
    builder.add_node("refuse", refuse)
    builder.add_node("validate", validate)
    builder.add_node("critic", critic)
    builder.add_node("assemble", assemble)
    builder.add_edge(START, "intake")
    # After intake, ask route_after_intake which node comes next. The mapping
    # {returned name: node name} is what lets LangGraph draw the branch.
    builder.add_conditional_edges("intake", route_after_intake,
                                  {"recall": "recall", "refuse": "refuse"})
    builder.add_edge("recall", "risk_review")
    builder.add_edge("risk_review", "convert")
    builder.add_conditional_edges("convert", route_after_convert,
                                  {"validate": "validate", "assemble": "assemble"})
    builder.add_edge("validate", "critic")
    builder.add_conditional_edges("critic", route_after_critic,
                                  {"convert": "convert", "assemble": "assemble"})
    builder.add_edge("assemble", END)
    builder.add_edge("refuse", END)
    return builder.compile(checkpointer=checkpointer, store=store)
