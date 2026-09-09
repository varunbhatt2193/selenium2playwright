"""Steps 8.1-8.2 — `s2p`: the command line, in Typer, with a rich scorecard and diff.

    uv run s2p convert samples/selenium-suite/pages/LoginPage.ts --out out/LoginPage.ts
    uv run s2p convert --thread login --refine "use data-testid locators"
    uv run s2p convert page.ts --model opus --json        # step 8.2: pick a model, get JSON
    uv run s2p remember "name page objects <Feature>Page"
    uv run s2p memories        # what it has been taught
    uv run s2p forget <key>    # drop one
    uv run s2p threads         # saved conversations

Until now the front end lived at the bottom of graph.py: argparse plus a column
of print()s. It worked, but it put the surface and the agent in one file, and
every new flag made graph.py less about the graph. This module is that surface,
moved out and rebuilt on two libraries:

  typer — the parser is the function signature. A parameter with a default is
          an option, one without is an argument, the type annotation is the
          converter and the validator, and the docstring is the help text. Each
          @app.command() function is a subcommand, which is why teaching and
          listing are now `s2p remember` / `s2p memories` rather than flags on a
          conversion that is not happening.
  rich  — the scorecard is a real table, the four gates and the critic in one
          grid, and the before/after is a syntax-highlighted diff.

Two rules the layout keeps from every earlier phase. Everything for the human
goes to **stderr**, so stdout stays exactly the converted TypeScript and
`s2p convert x.ts > x.spec.ts` is still a usable pipe. And nothing is silent:
a preference that was not close enough to apply, a risk nobody was asked about,
an unavailable critic — each one is said out loud, because the alternative is a
user wondering why their rule did nothing.

Step 8.2 adds the run-scoped settings — `--model`, `--critic-model`,
`--max-attempts` — and hands them to the graph as its *context*, not as state:
they describe how this one invocation runs, so they are typed fresh each time
and never restored from a thread. `--json` puts the whole outcome on stdout as
one document, for CI and for the callers Phase 9 and 10 will add.

Exit codes (unchanged): 0 = every gate and the critic passed with no open
TODO(review), 1 = needs-review, 2 = unsupported input or a usage error.
"""

from __future__ import annotations

import difflib
import json
import sys
from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Optional

import typer
from langgraph.types import Command
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from selenium2playwright import assemble, env, graph, memory, risk, suite, suite_graph
from selenium2playwright import store as memory_store
from selenium2playwright.llm import check_model, embedding_dims, make_embeddings
from selenium2playwright.one_shot import format_usage
from selenium2playwright.reflection import MAX_ATTEMPTS
from selenium2playwright.schemas import ConversionReport, ConversionResult

# stderr, and soft_wrap so a long finding is never folded or cropped: these
# lines are read by people and grepped by tests. highlight=False stops rich
# from colouring numbers and paths inside text we did not style ourselves.
console = Console(stderr=True, highlight=False, soft_wrap=True)

app = typer.Typer(
    add_completion=False, no_args_is_help=True, pretty_exceptions_enable=False,
    help="Convert TypeScript Selenium tests to Playwright, with compiler-verified output.",
)

# Shared option types. Written once here and reused by every command that takes
# them, so `--user` means the same thing and reads the same in all of them.
UserOption = Annotated[str, typer.Option("--user", help="whose long-term memories to use")]
MemoryDbOption = Annotated[Path, typer.Option(
    "--memory-db", help="long-term memory database (outlives threads, unlike --db)")]
ThreadDbOption = Annotated[Path, typer.Option("--db", help="thread (conversation) database")]

ModelOption = Annotated[str, typer.Option(
    "--model", metavar="NAME",
    help=f"actor model for this run: {' | '.join(env.MODEL_ALIASES)}, or a full provider:model")]

PASS_STYLE, FAIL_STYLE, MUTED = "bold green", "bold red", "dim"
# Column-width shorthand for the suite table; the JSON keeps the full word.
VERDICTS = {"needs-review": "REVIEW", "refused": "REFUSED", "failed": "FAILED"}
DIFF_LIMIT = 240  # a whole-file rewrite is long; past this, read the file itself
# Named and versioned so a consumer can check the shape it is reading rather
# than guessing from the keys; a breaking change gets /v2, never a silent edit.
REPORT_SCHEMA = "s2p.conversion-report/v1"


def say(*parts: object, style: str = "") -> None:
    """One stderr line, never parsed as rich markup.

    Model output, compiler messages and file paths all contain square brackets,
    which rich would otherwise read as style tags — Text is literal by
    construction, so this is the safe way to print something we did not write.
    """
    console.print(Text.assemble(*[(p, style) if isinstance(p, str) else p for p in parts]))


def mark(passed: bool, yes: str = "PASS", no: str = "FAIL") -> Text:
    return Text(yes if passed else no, style=PASS_STYLE if passed else FAIL_STYLE)


def show_thread(state: dict, db: Path, thread_id: str) -> None:
    """What this conversation remembers, so a resumed turn is never a black box."""
    say(f"Thread {thread_id!r} · turn {state.get('turn', 1)} · {db}", style=MUTED)
    if state.get("baseline") is not None:
        say("  continuing from the previous turn's conversion", style=MUTED)
    for number, convention in enumerate(state.get("conventions", []), 1):
        say(f"  standing instruction {number}: {convention}", style=MUTED)


def show_models(state: dict) -> None:
    """Which models actually ran. Always printed, because it is always a choice.

    The names come from the state, where intake recorded what it resolved, so
    this line cannot disagree with the model that was called (graph.model_for).
    """
    models = state.get("models", {})
    if not models:
        return
    actor, critic = models.get("actor", "?"), models.get("critic", "?")
    say(f"Models: actor {actor}" + ("" if critic == actor else f" · critic {critic}"), style=MUTED)


def show_recall(state: dict) -> None:
    """What long-term memory contributed — including when the answer is nothing.

    A preference that exists but was not close enough to this file is the
    interesting case: it silently did not apply, so it is said out loud, with
    the number, so "why did it not use my rule?" has an answer on screen.
    """
    recalled = state.get("recalled", [])
    total = state.get("memory_count", 0)
    if not total and not recalled:
        return
    if not recalled:
        say(f"Long-term memory: {total} remembered preference(s), none close enough to this "
            f"file (minimum score {memory_store.MIN_SCORE}). See `s2p memories`.")
        return
    say(f"Long-term memory: applying {len(recalled)} of {total} remembered preference(s)")
    for item in recalled:
        how = f"score {item.score:.2f}" if item.score is not None else "unranked — semantic recall off"
        say(f"  {item.key} ({how}) {item.text}")


def show_risks(state: dict) -> None:
    """What was flagged and what was decided — never a silent choice."""
    flagged = state.get("risks", [])
    if not flagged:
        return
    decisions = state.get("decisions", {})
    say(f"Risk review: {len(flagged)} pattern(s) with more than one correct conversion")
    for item in flagged:
        kind = risk.RISKS[item.kind]
        where = f"{kind.title} (line {item.line}"
        where += f", {item.count} occurrences)" if item.count > 1 else ")"
        if item.kind in decisions:
            say(f"  {where} → {decisions[item.kind] or kind.default.key}")
        else:
            say(f"  {where} — not asked; converted with the playbook default. "
                f"Rerun with --thread to be asked, or --answer {item.kind}=<key>.")


def scorecard(state: dict, report: ConversionReport) -> Table:
    """The four deterministic gates and the critic, in one grid.

    The gates are facts a compiler produced; the critic is a model's opinion,
    and it is a row rather than a headline for that reason. The verdict line
    above the table carries the attempt count, because "passed on lap 3" and
    "passed on lap 1" are different results — and it is a plain soft-wrapped
    line rather than a cell so that a long reason is never folded or cropped.
    """
    table = Table(title=f"{state['source_path']} → Playwright", title_justify="left")
    table.add_column("check")
    table.add_column("result")
    table.add_column("detail")
    if not report.validation:
        table.add_row("gates", Text("NOT RUN", style=FAIL_STYLE), "no converted file available")
    for check in report.validation:
        count = len(check.findings)
        # A passing gate can still carry findings: lint warnings are reported,
        # not fatal, and hiding them behind "clean" would lose them.
        table.add_row(check.gate, mark(check.passed),
                      f"{count} finding(s)" if count else
                      "clean" if check.passed else "see tool output below")
    critique = report.critique
    if critique is None:
        table.add_row("critic", Text("UNAVAILABLE", style=FAIL_STYLE),
                      state.get("critique_error") or "not run")
    else:
        table.add_row("critic", mark(critique.verdict == "pass", "PASS", "REVISE"),
                      f"{len(critique.fixes)} fix(es) requested" if critique.fixes else "no fixes requested")
    result = report.result
    todos = len(result.todos) if result is not None else 0
    table.add_row("TODO(review)", mark(not todos, "NONE", str(todos)),
                  "needs a human" if todos else "nothing left open")
    return table


def show_findings(report: ConversionReport) -> None:
    """Every finding and every requested fix, in full, under the table."""
    for check in report.validation:
        for finding in check.findings:
            say(f"  {check.gate}: {finding.render()}", style=FAIL_STYLE if not check.passed else "")
        # What the gate saw and did not count — a package this sandbox does not
        # install, a module in a file the run never touched. Kept out of the
        # model's feedback, because it cannot fix them; kept in front of the
        # person, because they are still true.
        for finding in check.excused:
            say(f"  {check.gate}: {finding.render()} (not this file's to fix)")
        if not check.passed and not check.findings and check.tool_output:
            say(check.tool_output)
    if report.critique is not None:
        for fix in report.critique.fixes:
            say(f"  fix: {fix}")
    for error in report.errors:
        say(f"  error: {error}", style=FAIL_STYLE)


def show_ledger(result: ConversionResult) -> None:
    """Notes and the consolidated TODO(review) ledger (playbook rule 25)."""
    for note in result.notes:
        say(f"  note: {note}", style=MUTED)
    for todo in result.todos:
        say(f"  {todo}")  # already worded "TODO(review): ..." by the ledger


def show_diff(before: str, after: str, before_label: str, after_label: str) -> None:
    """Before/after as a unified diff, syntax-highlighted.

    Selenium to Playwright is close to a full rewrite, so this is long by
    nature — that is the honest picture of what changed, and --no-diff turns it
    off. On a refine turn the "before" is the previous turn's output instead of
    the source, because what changed *this turn* is the interesting question.
    """
    lines = list(difflib.unified_diff(before.splitlines(), after.splitlines(),
                                      fromfile=before_label, tofile=after_label, lineterm="", n=2))
    if not lines:
        say("No change from the previous turn.", style=MUTED)
        return
    shown, hidden = lines[:DIFF_LIMIT], max(0, len(lines) - DIFF_LIMIT)
    body = "\n".join(shown) + (f"\n... {hidden} more diff line(s)" if hidden else "")
    console.print(Panel(Syntax(body, "diff", theme="ansi_dark", word_wrap=True),
                        title=f"{before_label} → {after_label}", title_align="left"))


def json_report(state: dict, destination: Path | None, exit_code: int) -> str:
    """The whole outcome as one JSON document — the machine-readable surface.

    Everything the scorecard shows is here as data, plus what a caller cannot
    see from the terminal: which models ran, which turn this was, what was
    recalled and decided. The converted file is inside it, at
    report.result.code, so `--json` alone is a complete answer and nothing has
    to be scraped off stderr.

    ConversionReport is a pydantic model, so its half serialises itself;
    Classification is a dataclass, hence asdict. A refused run is a real
    outcome, not an error: it produces the same document with report = null.
    """
    report = state.get("report")
    document = {
        "schema": REPORT_SCHEMA,
        "source": state.get("source_path", ""),
        "output": str(destination) if destination is not None else None,
        "status": "refused" if state["status"] == "refused" else report.status if report else "failed",
        "exit_code": exit_code,
        "refusal": state.get("refusal", ""),
        "classification": asdict(state["classification"]),
        "models": state.get("models", {}),
        "max_attempts": state.get("max_attempts", MAX_ATTEMPTS),
        "turn": state.get("turn", 1),
        "conventions": state.get("conventions", []),
        "recalled": [item.text for item in state.get("recalled", [])],
        "decisions": state.get("decisions", {}),
        "usage": {"conversion": state.get("usage"), "critic": state.get("critic_usage")},
        "report": report.model_dump(mode="json") if report is not None else None,
    }
    return json.dumps(document, indent=2)


def emit(state: dict, destination: Path | None, exit_code: int, as_json: bool,
         code: str | None) -> int:
    """stdout, exactly once: the JSON document, or the converted file, or nothing.

    Every return path in present() comes through here, which is what keeps the
    promise that stdout is machine-readable and complete — a refusal in --json
    mode is still a document, and --json never prints the raw code as well,
    because that would leave stdout holding two things at once.
    """
    if as_json:
        print(json_report(state, destination, exit_code))
    elif code is not None:
        print(code, end="")  # stdout: the file, and nothing else
    return exit_code


def present(state: dict, out: Path | None, show_diff_panel: bool, as_json: bool = False) -> int:
    """Everything the run produced, then the exit code it earned."""
    classification = state["classification"]
    say(f"[{classification.automation} · {classification.runner} · {classification.language}] "
        f"{classification.reason}", style=MUTED)
    show_models(state)
    show_recall(state)
    show_risks(state)
    # A resumed turn that was not given --out still knows where the file goes.
    destination = out or (Path(state["output_path"]) if state.get("output_path") else None)
    if state["status"] == "refused":
        say(f"✗ not converted: {state['refusal']}", style=FAIL_STYLE)
        return emit(state, None, 2, as_json, None)
    report = state["report"]
    say(f"Conversion: {report.status} ({report.attempts}/"
        f"{state.get('max_attempts', MAX_ATTEMPTS)} attempts) — {report.reason}",
        style=PASS_STYLE if report.status == "passed" else "bold yellow")
    console.print(scorecard(state, report))
    show_findings(report)
    if report.result is not None:
        show_ledger(report.result)
    for label, usage in (("Conversion", state.get("usage")), ("Critic", state.get("critic_usage"))):
        if usage:
            say(f"{label} tokens (all attempts): {format_usage(usage)}", style=MUTED)
    if report.result is None:
        say("No converted code was produced; no output file was written.", style=FAIL_STYLE)
        if state.get("baseline") is not None:
            # A failed refinement turn is not a lost conversion: the turn it was
            # refining is still in the thread and still on disk. Say so.
            say("The previous turn's conversion on this thread is unchanged.")
        return emit(state, None, 1, as_json, None)
    if show_diff_panel:
        baseline = state.get("baseline")
        before = baseline.code if baseline is not None else state["source"]
        label = "previous turn" if baseline is not None else Path(state["source_path"]).name
        show_diff(before, report.result.code, label, "converted")
    if destination is not None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(report.result.code, encoding="utf-8")
        say(f"[wrote {destination}]")
    return emit(state, destination, 0 if report.status == "passed" else 1, as_json,
                None if destination is not None else report.result.code)


def ask_human(payload: dict, interactive: bool) -> str:
    """Put one interrupt's question to the user; return their answer verbatim.

    Everything goes to stderr, and so does the input prompt, because stdout is
    reserved for the converted TypeScript. An empty answer means "the default",
    which is also what a run with no terminal gets — stated out loud, never
    silently.
    """
    say("")
    say(f"Paused — {payload['title']}", style="bold yellow")
    say(f"  found: {payload['evidence']}")
    say(f"  why you: {payload['why']}")
    say(f"  {payload['question']}")
    for number, option in enumerate(payload["options"], 1):
        default = " [default]" if option["key"] == payload["default"] else ""
        say(f"    {number}) {option['key']} — {option['label']}{default}")
    if not interactive:
        say(f"  no terminal to ask on; using the default ({payload['default']}). "
            f"Choose with --answer {payload['kind']}=<key>.")
        return ""
    console.print(Text("  answer (number, key, your own words, or Enter for the default): "), end="")
    answer = input().strip()
    if answer.isdigit() and 1 <= int(answer) <= len(payload["options"]):
        return payload["options"][int(answer) - 1]["key"]
    return answer


def parse_answers(values: list[str]) -> dict[str, str]:
    """--answer dialogs=auto-dismiss ... into {kind: answer}, for scripted runs."""
    answers = {}
    for value in values:
        kind, separator, text = value.partition("=")
        if not separator or kind not in risk.RISKS:
            raise typer.BadParameter(f"--answer must be KIND=ANSWER, KIND one of: {', '.join(risk.RISKS)}")
        answers[kind] = text.strip()
    return answers


def embeddings_for(writing: bool):
    """The configured embeddings model and its width, or (None, None).

    None is fine for reading — recall falls back to the most recent memories and
    says so. It is not fine for writing: a memory stored without a vector can
    never be found by a later semantic search, so that is a hard error with the
    two ways out, rather than a memory that quietly never comes back.
    """
    name = env.embeddings_name()
    if not name:
        return None, None
    try:
        model = make_embeddings()
        return model, embedding_dims(model)
    except Exception as exc:  # missing key, missing package, provider down
        if writing:
            raise typer.BadParameter(
                f"S2P_EMBEDDINGS={name} could not be loaded ({exc}); a memory written now could "
                "never be recalled. Fix its key, or set S2P_EMBEDDINGS=off to keep memories "
                "without semantic search.") from exc
        say(f"Long-term memory: {name} unavailable ({exc}); recall falls back to the most "
            "recent memories.")
        return None, None


def run_config(models: dict[str, str], max_attempts: int) -> dict:
    """The LangSmith half of the same choices: tags and metadata for the trace.

    Context tells the graph what to do; this tells the trace what was done, in
    the two forms LangSmith can search on — tags for filtering a list of runs,
    metadata for reading one. Without it, `--model opus` would be visible only
    by opening an individual LLM span and squinting at it.
    """
    return {"run_name": "conversion-graph",
            "tags": ["step:8.2", "prompt:v1", "critic:v1",
                     f"model:{models['actor'].split(':')[-1]}", f"attempts:{max_attempts}"],
            "metadata": {"actor_model": models["actor"], "critic_model": models["critic"],
                         "max_attempts": max_attempts},
            "recursion_limit": 3 * MAX_ATTEMPTS + 5}


def resolve_models(model: str, critic_model: str) -> dict[str, str]:
    """Turn the model settings into full names, and refuse an unusable one now.

    Every run is checked, not just one that names a model on the command line:
    the commonest way to arrive here is a fresh machine whose only key belongs
    to a different provider than the default, and finding that out from an
    authentication error after intake — as exit 1, a *conversion* verdict — is
    the wrong answer to a configuration question.

    llm.check_model does the asking, so a provider this project has never heard
    of gets its own client's answer — a missing integration package, a key under
    a variable env.py does not know — rather than a refusal from a table here.
    """
    try:
        names = env.resolve_roles(model, critic_model)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    for name in dict.fromkeys(names.values()):  # once per distinct model
        problem = check_model(name)
        if problem:
            raise typer.BadParameter(problem + alternatives())
    return names


def alternatives() -> str:
    """"…and here is what this machine could run instead", when anything is keyed."""
    ready = env.keyed_providers()
    if not ready:
        return ""
    return (f" Keys are set here for {', '.join(ready)} — run with --model {ready[0]}:<model>,"
            f" or put S2P_MODEL={ready[0]}:<model> in .env.")


@app.command()
def convert(
    source: Annotated[Optional[Path], typer.Argument(
        exists=True, dir_okay=False,
        help="the Selenium file; omit it to continue a saved --thread")] = None,
    context: Annotated[Optional[list[Path]], typer.Argument(
        exists=True, dir_okay=False, help="already-converted companion files")] = None,
    out: Annotated[Optional[Path], typer.Option(
        "--out", "-o", help="output file; also anchors relative imports to companions")] = None,
    thread: Annotated[Optional[str], typer.Option(
        "--thread", help="conversation id; saves this turn and resumes the last one")] = None,
    refine: Annotated[str, typer.Option(
        "--refine", metavar="INSTRUCTION",
        help='a standing instruction for this thread, e.g. "use data-testid locators"')] = "",
    answer: Annotated[Optional[list[str]], typer.Option(
        "--answer", metavar="KIND=ANSWER",
        help="answer a risk question up front, e.g. --answer dialogs=auto-dismiss")] = None,
    ask: Annotated[bool, typer.Option(
        "--ask/--no-ask",
        help="pause on a flagged pattern (needs --thread); --no-ask uses the playbook default")] = True,
    remember: Annotated[str, typer.Option(
        "--remember", metavar="PREFERENCE",
        help="file a preference in long-term memory: it applies now and in every future run")] = "",
    user: UserOption = memory_store.DEFAULT_USER,
    recall: Annotated[bool, typer.Option(
        "--recall/--no-recall", help="use long-term memory; --no-recall reads and writes nothing")] = True,
    max_attempts: Annotated[int, typer.Option(
        "--max-attempts", min=1, max=MAX_ATTEMPTS,
        help="total conversion attempts: 1 = no repairs; 3 = draft plus two repairs")] = MAX_ATTEMPTS,
    model: ModelOption = "",
    critic_model: Annotated[str, typer.Option(
        "--critic-model", metavar="NAME",
        help="reviewer model; defaults to the actor unless S2P_CRITIC_MODEL says otherwise")] = "",
    as_json: Annotated[bool, typer.Option(
        "--json", help="put the whole outcome on stdout as one JSON document, code included")] = False,
    diff: Annotated[bool, typer.Option("--diff/--no-diff", help="show the before/after diff")] = True,
    db: ThreadDbOption = memory.DEFAULT_DB,
    memory_db: MemoryDbOption = memory_store.DEFAULT_DB,
) -> None:
    """Convert one Selenium file, validate it, and repair it up to three times."""
    if remember and not recall:
        raise typer.BadParameter("--no-recall turns long-term memory off; it cannot be "
                                 "combined with --remember")
    if source is None and not thread:
        raise typer.BadParameter("give a source file, or --thread <id> to continue a saved conversation")
    companions = list(context or [])
    if out and source is not None and out.resolve() in {p.resolve() for p in [source, *companions]}:
        raise typer.BadParameter("--out must differ from the source and companion files")
    models = resolve_models(model, critic_model)

    # Only keys the caller actually supplied: anything omitted on a later turn
    # keeps the value the checkpointer restored, which is the whole point.
    inputs: dict = {"user_id": user}
    if source is not None:
        inputs["source_path"] = str(source)
        inputs["context_paths"] = [str(p) for p in companions]
    if out:
        inputs["output_path"] = str(out)
    if refine:
        inputs["refinement"] = refine
    if remember:
        inputs["remember"] = remember
    answers = parse_answers(answer or [])
    # Pausing needs a checkpointer to pause into, so only a --thread run can ask.
    inputs["ask_risks"] = bool(thread) and ask
    # The run-scoped half of this call: how it runs, not what it is about. It is
    # handed to invoke() as context, so nothing here is written to the thread
    # and every turn is free to choose again (graph.RunSettings).
    run = graph.RunSettings(model=models["actor"], critic_model=models["critic"],
                            max_attempts=max_attempts)
    config = run_config(models, max_attempts)

    with ExitStack() as stack:
        checkpointer = stack.enter_context(memory.open_checkpointer(db)) if thread else None
        store = None
        if recall:
            embeddings, dims = embeddings_for(bool(remember))
            store = stack.enter_context(memory_store.open_store(memory_db, embeddings, dims))
        compiled = graph.build_graph(checkpointer, store)
        if thread:
            config = memory.thread_config(thread, **config)
            saved = memory.thread_state(compiled, thread)
            if source is None and not saved.get("source_path"):
                raise typer.BadParameter(f"thread {thread!r} has no saved conversion yet; "
                                         "pass a source file to start it")
            # Merge, do not replace: an answer given on turn 1 still stands.
            if answers or saved.get("decisions"):
                inputs["decisions"] = {**saved.get("decisions", {}), **answers}
        elif answers:
            inputs["decisions"] = answers
        final = compiled.invoke(inputs, config=config, context=run)
        # A paused invoke returns whatever it managed to write plus the question,
        # and no report: the run is suspended on the thread, not finished. Answer,
        # resume the same thread, repeat — at most one pause per risk kind,
        # because risk_review asks each question exactly once.
        interactive = sys.stdin.isatty()
        for _ in range(len(risk.RISKS)):
            if "__interrupt__" not in final:
                break
            final = compiled.invoke(Command(resume=ask_human(final["__interrupt__"][0].value, interactive)),
                                    config=config, context=run)
        if "__interrupt__" in final:
            say("Still waiting on an answer after every question; nothing was converted.",
                style=FAIL_STYLE)
            raise typer.Exit(2)

    if thread:
        show_thread(final, db, thread)
    raise typer.Exit(present(final, out, diff, as_json))


@app.command()
def scan(
    root: Annotated[Path, typer.Argument(
        exists=True, file_okay=False,
        help="the Selenium suite folder to read")],
    out: Annotated[Optional[Path], typer.Option(
        "--out", "-o", help="write the manifest JSON here as well")] = None,
    as_json: Annotated[bool, typer.Option(
        "--json", help="print the manifest JSON to stdout instead of a table")] = False,
) -> None:
    """Read a suite folder and print the conversion plan. No model is called.

    This is the step before `convert` ever runs on a folder: what is in here,
    what can be converted, and in what order. The table is for a person; --json
    is the same plan as data, which is what step 9.2 dispatches from.
    """
    manifest = suite.scan(root)
    payload = json.dumps(suite.manifest_json(manifest), indent=2)
    if as_json:
        print(payload)
    else:
        show_manifest(manifest)
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload + "\n", encoding="utf-8")
        say(f"[wrote {out}]")
    raise typer.Exit(0 if manifest.convertible else 1)


def show_manifest(manifest: suite.Manifest) -> None:
    """The plan as a table: one section per wave, then what we are not converting.

    Waves are printed in the order they will run, because that order is the
    single most important thing on this screen — everything in wave 1 can be
    converted at the same time, and nothing in wave 2 may start until wave 1 is
    done.
    """
    counts = manifest.counts()
    say(f"Suite {manifest.root} · {len(manifest.files)} source file(s) · "
        f"{counts['convert']} to convert, {counts['copy']} to copy, {counts['skip']} skipped")
    by_path = {f.path: f for f in manifest.files}

    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    for column in ("wave", "file", "kind", "lines", "needs first"):
        table.add_column(column, overflow="fold")
    for number, wave in enumerate(manifest.waves, 1):
        for path in wave:
            item = by_path[path]
            needs = ", ".join(item.imports) or "—"
            table.add_row(str(number), path, item.kind, str(item.lines), needs)
    if manifest.waves:
        console.print(table)
    for item in manifest.files:
        if item.action != suite.CONVERT:
            verb = "copy" if item.action == suite.COPY else "skip"
            say(f"  {verb} {item.path} — {item.reason}",
                style="" if item.action == suite.COPY else MUTED)
    for note in manifest.notes:
        say(f"  note: {note}", style=MUTED)
    if not manifest.convertible:
        say("Nothing to convert in this folder.", style=FAIL_STYLE)


@app.command("suite")
def convert_suite(
    root: Annotated[Path, typer.Argument(
        exists=True, file_okay=False, help="the Selenium suite folder to convert")],
    out: Annotated[Path, typer.Option(
        "--out", "-o", help="where the converted tree goes; created if it does not exist")],
    only: Annotated[Optional[list[str]], typer.Option(
        "--only", metavar="PATTERN",
        help="convert just these files, e.g. --only 'pages/*.ts' --only LoginPage.ts")] = None,
    parallel: Annotated[int, typer.Option(
        "--parallel", min=1, max=16,
        help="how many files of one wave to convert at the same time")] = 4,
    max_attempts: Annotated[int, typer.Option(
        "--max-attempts", min=1, max=MAX_ATTEMPTS,
        help="conversion attempts per file: 1 = no repairs; 3 = draft plus two repairs")] = MAX_ATTEMPTS,
    model: ModelOption = "",
    critic_model: Annotated[str, typer.Option(
        "--critic-model", metavar="NAME",
        help="reviewer model; defaults to the actor unless S2P_CRITIC_MODEL says otherwise")] = "",
    user: UserOption = memory_store.DEFAULT_USER,
    recall: Annotated[bool, typer.Option(
        "--recall/--no-recall", help="use long-term memory; --no-recall reads and writes nothing")] = True,
    report: Annotated[Optional[Path], typer.Option(
        "--report", metavar="FILE",
        help=f"where the markdown report goes; default <out>/{assemble.REPORT_NAME}")] = None,
    as_json: Annotated[bool, typer.Option(
        "--json", help="put the whole run on stdout as one JSON document")] = False,
    memory_db: MemoryDbOption = memory_store.DEFAULT_DB,
) -> None:
    """Convert a whole folder: page objects first, then the tests that import them.

    `s2p scan` shows the plan; this runs it. Each wave of independent files is
    dispatched in parallel and every file goes through the same graph `s2p
    convert` uses, so the per-file result is the same result — there is just one
    trace holding all of them (step 9.2).

    When every wave is done the tree is assembled (step 9.3): compiled as one
    project, added up into a scorecard, diffed against the source for public API
    that went missing, and written out as a markdown report next to the code.
    """
    if out.resolve() == root.resolve():
        raise typer.BadParameter("--out must be a different folder from the suite being converted")
    if root.resolve() in out.resolve().parents:
        raise typer.BadParameter("--out must not be inside the suite being converted")
    models = resolve_models(model, critic_model)
    patterns = list(only or [])
    # Scanned here rather than only inside the graph, so a folder with nothing
    # to convert — or an --only that matches nothing — is answered before a
    # model is built, and the plan node is handed the manifest instead of
    # reading every file a second time.
    planned = suite.scan(root)
    chosen = [f for f in planned.convertible if suite_graph.selected(f.path, patterns)]
    if not planned.convertible:
        show_manifest(planned)
        raise typer.Exit(1)
    if not chosen:
        raise typer.BadParameter(
            f"--only matched none of the {len(planned.convertible)} convertible file(s); "
            f"run `s2p scan {root}` to see them")
    inputs = {"root": str(root), "out_root": str(out), "only": patterns, "manifest": planned,
              "report_path": str(report) if report else ""}
    run = suite_graph.SuiteSettings(model=models["actor"], critic_model=models["critic"],
                                    max_attempts=max_attempts, user_id=user if recall else "")

    with ExitStack() as stack:
        store = None
        if recall:
            embeddings, dims = embeddings_for(writing=False)
            store = stack.enter_context(memory_store.open_store(memory_db, embeddings, dims))
        compiled = suite_graph.build_suite_graph(store)
        say(f"Suite {root} → {out} · {len(chosen)} file(s) · {len(planned.waves)} wave(s) · "
            f"{parallel} at a time · up to {max_attempts} attempt(s) each")
        say(f"Models: actor {models['actor']}"
            + ("" if models["critic"] == models["actor"] else f" · critic {models['critic']}"),
            style=MUTED)
        final = compiled.invoke(inputs, context=run, config=suite_run_config(
            models, max_attempts, len(planned.waves), parallel))

    raise typer.Exit(present_suite(final, out, as_json))


def suite_run_config(models: dict[str, str], max_attempts: int, waves: int, parallel: int) -> dict:
    """Trace tags and the two limits that keep a many-file run inside its bounds.

    max_concurrency is what actually caps the fan-out: LangGraph will start
    every Send in a wave at once otherwise, and a forty-file wave would open
    forty connections to the provider. recursion_limit counts super-steps, and
    each wave costs two of them (dispatch, then the join), so it grows with the
    suite rather than being a number that works until it does not.
    """
    return {"run_name": "suite-graph",
            "tags": ["step:9.3", f"model:{models['actor'].split(':')[-1]}", f"waves:{waves}"],
            "metadata": {"actor_model": models["actor"], "critic_model": models["critic"],
                         "max_attempts": max_attempts, "waves": waves, "parallel": parallel},
            "max_concurrency": parallel,
            "recursion_limit": 2 * waves + 6}


def present_suite(final: dict, out: Path, as_json: bool) -> int:
    """The run as a table, then what the whole tree adds up to, then the exit code.

    One row per file in plan order — which is not the order they finished in,
    because they ran at the same time (suite_graph.ordered re-sorts them).
    Exit 0 needs two things now: every file passed outright **and** the finished
    tree compiles as one project. A per-file pass is a claim about one file; the
    tree compile is the claim about the thing being handed over, and a folder
    that does not build is not a delivery whatever its rows say.
    """
    outcomes = suite_graph.ordered(final)
    counts = suite_graph.totals(outcomes)
    built = final.get("assembly") or assemble.Assembly()
    failed = len(outcomes) - counts["passed"]
    exit_code = 0 if not failed and built.compiles else 1

    # The table is printed either way, for the same reason `s2p convert` prints
    # its scorecard under --json: stderr is for the person watching, stdout is
    # the document, and the two never compete for the same stream.
    console.print(suite_table(outcomes))
    for outcome in outcomes:
        for error in outcome.errors:
            say(f"  {outcome.path}: error: {error}", style=FAIL_STYLE)
        if outcome.status in ("refused", "failed"):
            say(f"  {outcome.path}: {outcome.reason}", style=FAIL_STYLE)
    for path in final.get("copied", []):
        say(f"  copied {path} unchanged", style=MUTED)
    for item in final["manifest"].files:
        if item.action == suite.SKIP:
            say(f"  skipped {item.path} — {item.reason}", style=MUTED)
    say(f"{len(outcomes)} file(s): " + " · ".join(f"{n} {name}" for name, n in counts.items() if n)
        + f" in {final.get('elapsed', 0.0):.1f}s",
        style=PASS_STYLE if not failed else "bold yellow")
    show_assembly(built)
    for label, role in (("Conversion", "usage"), ("Critic", "critic_usage")):
        usage = suite_graph.suite_usage(outcomes, role)
        if usage:
            say(f"{label} tokens (whole suite): {format_usage(usage)}", style=MUTED)
    say(f"[wrote {out}]")
    if built.report_path:
        say(f"[wrote {built.report_path}]")
    if as_json:
        print(json.dumps(assemble.report_json(suite_graph.run_json(final, exit_code), built), indent=2))
    return exit_code


def show_assembly(built: assemble.Assembly) -> None:
    """The three suite-wide answers, in the order they change a decision.

    The whole-tree compile first, because it is the one that can turn twelve
    green rows into an unusable folder. Then what the conversion did to the
    public surface, then the one list of everything still open.
    """
    if built.tree is None:
        say(f"Whole tree: not compiled — {built.tree_error}", style=FAIL_STYLE)
    elif built.tree.passed:
        excused = len(built.tree.excused)
        aside = f", {excused} error(s) in absent packages or carried files" if excused else ""
        say(f"Whole tree: {built.files} file(s) compile together, no errors{aside}",
            style=PASS_STYLE)
    else:
        findings = built.tree.findings
        say(f"Whole tree: {len(findings)} error(s) compiling {built.files} file(s) together",
            style=FAIL_STYLE)
        for finding in findings[:5]:
            say(f"  {finding.render()}", style=FAIL_STYLE)
        if len(findings) > 5:
            say(f"  …and {len(findings) - 5} more; the report has all of them", style=FAIL_STYLE)

    api = built.scorecard.get("api", {})
    if any(api.values()):
        unexplained = built.scorecard.get("unexplained_removals", 0)
        removed = api.get("removed", 0)
        say(f"Public API: {api.get('kept', 0)} kept · {api.get('renamed', 0)} renamed · "
            f"{removed} removed" + (f" ({unexplained} with no reason given)" if unexplained else ""),
            style=FAIL_STYLE if unexplained else MUTED)
        for ledger in built.ledgers:
            for change in ledger.losses:
                say(f"  {ledger.path}: {change.name} — "
                    + (f"now {change.counterpart}" if change.verdict == "renamed"
                       else f"removed, {change.reason or 'no reason given'}"),
                    style=MUTED if change.verdict == "renamed" or change.reason else FAIL_STYLE)

    if built.todos:
        say(f"TODO(review): {len(built.todos)} task(s) still open")
        for number, todo in enumerate(built.todos, 1):
            # A task can run to a paragraph; the terminal gets the first line of
            # it and the report next to the code gets all of it.
            text = todo.text if len(todo.text) <= 110 else todo.text[:109] + "…"
            say(f"  {number}. {text} · " + ", ".join(todo.places), style=MUTED)
    for note in built.notes:
        say(f"  note: {note}", style=MUTED)


def suite_table(outcomes: list[suite_graph.FileOutcome]) -> Table:
    """One row per file: what happened, which gates held, and how long it took."""
    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    for column in ("wave", "file", "result", "laps", "gates", "critic", "TODO", "secs"):
        table.add_column(column, overflow="fold")
    for outcome in outcomes:
        table.add_row(
            str(outcome.wave), outcome.path,
            mark(outcome.ok, "PASS", VERDICTS.get(outcome.status, outcome.status.upper())),
            str(outcome.attempts), gates_cell(outcome),
            Text(outcome.critic.upper(), style=PASS_STYLE if outcome.critic == "pass" else FAIL_STYLE)
            if outcome.critic else Text("—", style=MUTED),
            str(len(outcome.todos)), f"{outcome.seconds:.0f}")
    return table


def gates_cell(outcome: suite_graph.FileOutcome) -> Text:
    """`4/4` when they all held, otherwise the score and the gates that did not.

    Naming only the failures keeps the column narrow enough that the filenames
    beside it stay on one line, and puts the useful half of the answer — which
    gate — where a reader is already looking.
    """
    if not outcome.gates:
        return Text("—", style=MUTED)
    failed = [gate for gate, ok in outcome.gates if not ok]
    score = f"{len(outcome.gates) - len(failed)}/{len(outcome.gates)}"
    if not failed:
        return Text(score, style=PASS_STYLE)
    return Text(f"{score} {' '.join(failed)}", style=FAIL_STYLE)


@app.command()
def remember(
    preference: Annotated[str, typer.Argument(help="the preference, in your own words")],
    user: UserOption = memory_store.DEFAULT_USER,
    memory_db: MemoryDbOption = memory_store.DEFAULT_DB,
) -> None:
    """Teach a preference once; every future conversation recalls it by itself."""
    embeddings, dims = embeddings_for(writing=True)
    with memory_store.open_store(memory_db, embeddings, dims) as store:
        saved = memory_store.remember(store, preference, user, source="cli")
    say(f"remembered [{saved.key}] {saved.text}")


@app.command("memories")
def list_memories(
    user: UserOption = memory_store.DEFAULT_USER,
    memory_db: MemoryDbOption = memory_store.DEFAULT_DB,
) -> None:
    """List everything remembered for this user, newest first.

    Keys and text go to stdout, not stderr: this output is a list you pipe,
    grep, or copy a key out of for `s2p forget`.
    """
    with memory_store.open_store(memory_db) as store:
        for item in memory_store.memories(store, user):
            print(f"{item.key}  {item.text}")


@app.command()
def forget(
    key: Annotated[str, typer.Argument(help="the key printed by `s2p memories`")],
    user: UserOption = memory_store.DEFAULT_USER,
    memory_db: MemoryDbOption = memory_store.DEFAULT_DB,
) -> None:
    """Delete one remembered preference. Listing and deleting need no embeddings."""
    with memory_store.open_store(memory_db) as store:
        gone = memory_store.forget(store, key, user)
    say(f"forgot [{key}]" if gone else f"no memory [{key}] to forget")


@app.command("threads")
def list_saved_threads(db: ThreadDbOption = memory.DEFAULT_DB) -> None:
    """List saved conversation ids, for `s2p convert --thread <id> --refine ...`."""
    for thread_id in memory.list_threads(db):
        print(thread_id)


def run(argv: list[str] | None = None) -> int:
    """Run the CLI and return its exit code instead of exiting the process.

    The console script entry point is `app` itself; this is the seam tests and
    scripts call, because click always ends a command by raising SystemExit and
    a test wants the number, not a stack unwind.
    """
    try:
        app(args=argv if argv is not None else sys.argv[1:], prog_name="s2p")
    except SystemExit as stop:
        return int(stop.code or 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
