"""Step 8.1 — `s2p`: the command line, in Typer, with a rich scorecard and diff.

    uv run s2p convert samples/selenium-suite/pages/LoginPage.ts --out out/LoginPage.ts
    uv run s2p convert --thread login --refine "use data-testid locators"
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

Exit codes (unchanged): 0 = every gate and the critic passed with no open
TODO(review), 1 = needs-review, 2 = unsupported input or a usage error.
"""

from __future__ import annotations

import difflib
import sys
from contextlib import ExitStack
from pathlib import Path
from typing import Annotated, Optional

import typer
from langgraph.types import Command
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from selenium2playwright import env, graph, memory, risk
from selenium2playwright import store as memory_store
from selenium2playwright.llm import embedding_dims, make_embeddings
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

PASS_STYLE, FAIL_STYLE, MUTED = "bold green", "bold red", "dim"
DIFF_LIMIT = 240  # a whole-file rewrite is long; past this, read the file itself


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


def present(state: dict, out: Path | None, show_diff_panel: bool) -> int:
    """Everything the run produced, then the exit code it earned."""
    classification = state["classification"]
    say(f"[{classification.automation} · {classification.runner} · {classification.language}] "
        f"{classification.reason}", style=MUTED)
    show_recall(state)
    show_risks(state)
    if state["status"] == "refused":
        say(f"✗ not converted: {state['refusal']}", style=FAIL_STYLE)
        return 2
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
    # A resumed turn that was not given --out still knows where the file goes.
    destination = out or (Path(state["output_path"]) if state.get("output_path") else None)
    if report.result is None:
        say("No converted code was produced; no output file was written.", style=FAIL_STYLE)
        if state.get("baseline") is not None:
            # A failed refinement turn is not a lost conversion: the turn it was
            # refining is still in the thread and still on disk. Say so.
            say("The previous turn's conversion on this thread is unchanged.")
        return 1
    if show_diff_panel:
        baseline = state.get("baseline")
        before = baseline.code if baseline is not None else state["source"]
        label = "previous turn" if baseline is not None else Path(state["source_path"]).name
        show_diff(before, report.result.code, label, "converted")
    if destination is not None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(report.result.code, encoding="utf-8")
        say(f"[wrote {destination}]")
    else:
        print(report.result.code, end="")  # stdout: the file, and nothing else
    return 0 if report.status == "passed" else 1


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

    # Only keys the caller actually supplied: anything omitted on a later turn
    # keeps the value the checkpointer restored, which is the whole point.
    inputs: dict = {"max_attempts": max_attempts, "user_id": user}
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
    config = {"run_name": "conversion-graph", "tags": ["step:8.1", "prompt:v1", "critic:v1"],
              "recursion_limit": 3 * MAX_ATTEMPTS + 5}

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
        final = compiled.invoke(inputs, config=config)
        # A paused invoke returns whatever it managed to write plus the question,
        # and no report: the run is suspended on the thread, not finished. Answer,
        # resume the same thread, repeat — at most one pause per risk kind,
        # because risk_review asks each question exactly once.
        interactive = sys.stdin.isatty()
        for _ in range(len(risk.RISKS)):
            if "__interrupt__" not in final:
                break
            final = compiled.invoke(Command(resume=ask_human(final["__interrupt__"][0].value, interactive)),
                                    config=config)
        if "__interrupt__" in final:
            say("Still waiting on an answer after every question; nothing was converted.",
                style=FAIL_STYLE)
            raise typer.Exit(2)

    if thread:
        show_thread(final, db, thread)
    raise typer.Exit(present(final, out, diff))


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
