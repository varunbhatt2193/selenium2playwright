"""Step 10.3 — everything the playground does, minus the buttons.

`ui/app.py` is a Streamlit app: it draws a paste box, some sample buttons, a
scorecard and two thumbs. This module is everything that happens *behind* those
widgets — building the request, streaming the run, reading the scorecard out of
the final state, asking the deployment what is left of today's budget, and
sending a 👍/👎 back.

The split is not tidiness. Streamlit re-runs the whole script top to bottom on
every click, which makes a Streamlit file an awkward place to put logic and an
impossible place to unit-test it: importing `ui/app.py` *is* running the app.
Everything here is ordinary functions over ordinary data, so `tests/
test_playground.py` can hand them a fake client and assert on what comes back,
and the app file stays a layout.

## What the playground is allowed to do

It talks to the deployment as a **visitor**, not as the owner — `S2P_DEMO_KEY`,
never `S2P_API_KEY`. Step 10.4 made that a meaningfully smaller set of powers:
inline text only, no server paths, no writes to shared memory, metered per
visitor and against a daily dollar budget. So the constraints in here
(`bare_name`, the byte cap, `X-S2P-Visitor`) are not the playground being
careful on its own initiative — they are the client half of a contract the
server enforces anyway. Doing it here too is what turns a 403 that reads like a
crash into a sentence typed under a text box before anything is sent.

The key stays on the server the app runs on. A person using the playground
never has one, which is the point: the demo is a URL you can put on a CV, not a
credential you have to hand out with it.
"""

from __future__ import annotations

import difflib
import io
import os
import re
import uuid
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import urlsplit

import httpx
from langgraph_sdk import get_sync_client

from selenium2playwright import assemble, env, suite, suite_graph

# `langgraph up` puts the production image here; the Fly deployment answers on
# https. The playground has no idea which one it is talking to, which is what
# makes "run it against your laptop first" a real workflow rather than a claim.
DEFAULT_URL = "http://127.0.0.1:8123"

# A conversion is three model calls on a bad day, and the reflection loop can
# sit for a minute without sending a byte down the stream. httpx's default read
# timeout is five seconds, which would abandon every real run at the exact
# moment it started working. Connect stays short — an unreachable backend should
# say so immediately rather than making somebody wait five minutes to find out.
TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=60.0, pool=10.0)

# The graph refuses anything larger (graph.MAX_SOURCE_BYTES) and so does the
# playground, one step earlier, so an oversized paste costs a sentence instead
# of a round trip.
MAX_BYTES = 256 * 1024

# guard._BARE_FILENAME, restated. A demo caller may name their file but may not
# name a *path*, because with no `source_text` the graph would open it on the
# server. Checking it here is what makes the refusal a hint next to the input
# rather than a 403 after the click.
BARE_NAME = re.compile(r"^(?!\.)[A-Za-z0-9._-]{1,255}$")

# Where a run's node names come from, translated. The graph's node names are
# accurate and mean nothing to a visitor; these say what the agent is doing at a
# moment when the only alternative is a spinner.
NODE_LABELS = {
    "intake": "Reading the file and working out what it is",
    "recall": "Checking long-term memory for relevant preferences",
    "risk_review": "Checking patterns that have more than one right answer",
    "convert": "Converting to Playwright",
    "validate": "Running the four gates: compile, residue, lint, parity",
    "critic": "A second model reviews the result",
    "assemble": "Writing the report",
    "refuse": "Refusing, with a reason",
}


@dataclass(frozen=True)
class Sample:
    """One button: a real file from the repository, and why it is interesting.

    `blurb` is the whole reason the samples exist. A paste box in front of a
    stranger is a blank page — the buttons are there so somebody who has never
    seen Selenium can still watch the thing work in one click, and so somebody
    who *has* can go straight to the case they doubt it handles.

    `companion` is the other half of a spec. Every test in the sample suite
    imports its page object, so a spec converted *alone* cannot compile: the
    file it imports does not exist on the server, `tsc` says so, and the run
    ends `needs-review` for a reason that is about the paste box rather than
    about the conversion. Sending the already-converted page object alongside
    is exactly what the suite graph does in wave 2, and it is what makes a
    single spec an honest demo instead of a rigged failure.
    """

    name: str
    path: Path
    blurb: str
    companion: Path | None = None

    def read(self) -> str:
        return self.path.read_text(encoding="utf-8")

    def context(self) -> dict[str, str]:
        """The converted companion this file needs, ready for `context_text`."""
        if self.companion is None or not self.companion.is_file():
            return {}
        return {self.companion.name: self.companion.read_text(encoding="utf-8")}


_SUITE = env.REPO_ROOT / "samples" / "selenium-suite"
_GOLDEN = env.REPO_ROOT / "samples" / "playwright-golden"

# Chosen for range, not for ease: two of them (alerts, windows) are patterns
# with no direct Playwright twin, and the login pair shows a page object and the
# spec that drives it converting into the same idiom.
_CATALOGUE = [
    ("pages/LoginPage.ts", "A page object: explicit waits, By locators, and helpers a test calls.", ""),
    ("tests/login.spec.ts", "A Mocha spec: driver setup and teardown, assertions, page object. "
                            "Sent with its already-converted page object, the way the suite does it.",
     "pages/LoginPage.ts"),
    ("pages/AlertsPage.ts", "Native dialogs. switchTo().alert() has no Playwright twin — it becomes an event handler.", ""),
    ("pages/WindowsPage.ts", "New tabs and window handles, which Playwright models as pages on a context.", ""),
    ("pages/DynamicLoadingPage.ts", "Explicit waits, most of which Playwright's auto-waiting deletes outright.", ""),
    ("pages/UploadPage.ts", "sendKeys() to a file input, which becomes setInputFiles().", ""),
]


def samples() -> list[Sample]:
    """The sample buttons, skipping any file that is not on this machine.

    The playground normally runs from a checkout, so all six are there. It does
    not *have* to — someone can run it against the deployment from anywhere —
    and a missing samples directory should cost the buttons, not the app.
    """
    found = []
    for relative, blurb, companion in _CATALOGUE:
        path = _SUITE / relative
        if path.is_file():
            found.append(Sample(
                name=Path(relative).name,
                path=path,
                blurb=blurb,
                companion=(_GOLDEN / companion) if companion else None,
            ))
    return found


# --- talking to the deployment ------------------------------------------------


def backend_url() -> str:
    return (os.environ.get("LANGGRAPH_DEPLOYMENT_URL") or DEFAULT_URL).rstrip("/")


def demo_key() -> str:
    """The key the playground calls with. Deliberately the demo one.

    `S2P_API_KEY` would also work and would be a mistake: the owner key bypasses
    the meter, so a playground holding it would run the whole day's budget past
    every guardrail step 10.4 built. It is only accepted as a fallback for a
    server running with `S2P_AUTH=off`, where neither key means anything.
    """
    return (os.environ.get("S2P_DEMO_KEY") or os.environ.get("S2P_API_KEY") or "").strip()


def new_visitor() -> str:
    """One name per browser session, in the shape `guard._VISITOR_OK` accepts.

    Sent as `X-S2P-Visitor` so the per-visitor limits (3/60s, 10/day) count
    people rather than counting the playground's server, which every visitor
    shares. It is not an identity and does not try to be one — someone who
    wants a second allowance can open a second tab. The daily *budget* is the
    limit that cannot be got around, and that one is global.
    """
    return f"pg-{uuid.uuid4().hex[:12]}"


def client(url: str = "", key: str = "", visitor: str = ""):
    """A LangGraph SDK client aimed at the deployment, wearing a visitor's name."""
    return get_sync_client(
        url=url or backend_url(),
        api_key=key or demo_key(),
        headers={"X-S2P-Visitor": visitor} if visitor else None,
        timeout=TIMEOUT,
    )


def headers(key: str = "", visitor: str = "") -> dict[str, str]:
    """Plain HTTP headers for the two routes the SDK knows nothing about.

    `GET /limits` and `POST /feedback` are ours (`http_app.py`), merged into the
    server beside the graph API, so they are reached with httpx rather than
    through the SDK. They authenticate themselves and accept either spelling of
    the token; Bearer is the one a human would type into curl.
    """
    sent = {"Authorization": f"Bearer {key or demo_key()}"}
    if visitor:
        sent["X-S2P-Visitor"] = visitor
    return sent


def fetch_limits(url: str = "", key: str = "", visitor: str = "") -> dict[str, Any]:
    """What is left of today's budget, or a dict saying why we do not know.

    Never raises. This is the first call the app makes and it draws a line in a
    sidebar; a backend that is down or a key that is wrong should turn that line
    into an explanation, not into a stack trace where the page should be.
    """
    try:
        response = httpx.get(
            f"{(url or backend_url()).rstrip('/')}/limits",
            headers=headers(key, visitor),
            timeout=15,
        )
    except Exception as exc:  # noqa: BLE001
        return {"error": f"The backend did not answer ({type(exc).__name__})."}
    if response.status_code != 200:
        return {"error": explain_status(response.status_code, _detail(response))}
    try:
        return response.json()
    except Exception:  # noqa: BLE001
        return {"error": "The backend answered something that was not JSON."}


def budget_line(snapshot: dict[str, Any]) -> str:
    """One sentence a visitor can read before spending any of somebody's money."""
    if "error" in snapshot:
        return snapshot["error"]
    budget = snapshot.get("budget") or {}
    remaining, limit = budget.get("remaining", 0), budget.get("limit", 0)
    dollars = snapshot.get("budget_usd_per_day")
    money = f" (${dollars:g}/day)" if isinstance(dollars, (int, float)) else ""
    if remaining <= 0:
        return f"Today's demo budget{money} is spent. It resets at midnight UTC."
    return f"{remaining} of {limit} conversions left today{money}."


def visitor_line(snapshot: dict[str, Any]) -> str:
    if "error" in snapshot:
        return ""
    per = snapshot.get("per_visitor") or {}
    return (
        f"Per visitor: {per.get('daily', '?')} a day, "
        f"{per.get('burst', '?')} per {per.get('window_s', '?')}s."
    )


def send_feedback(
    run_id: str,
    score: float,
    *,
    comment: str = "",
    source_text: str = "",
    source_path: str = "",
    url: str = "",
    key: str = "",
    visitor: str = "",
) -> dict[str, Any]:
    """👍 or 👎 on one run. Sends the input too, so a 👎 is worth something.

    The score alone is a number on a chart. The *file somebody said we got
    wrong* is a dataset row, and `feedback.py` queues it into
    `s2p-feedback-queue` for exactly that reason — which only works if the
    playground bothers to send back what it submitted.
    """
    try:
        response = httpx.post(
            f"{(url or backend_url()).rstrip('/')}/feedback",
            headers=headers(key, visitor),
            json={
                "run_id": run_id,
                "score": score,
                "comment": comment,
                "source_text": source_text if score < 1 else "",
                "source_path": source_path,
            },
            timeout=30,
        )
    except Exception as exc:  # noqa: BLE001
        return {"stored": False, "queued": False,
                "detail": f"Could not reach the backend ({type(exc).__name__})."}
    if response.status_code != 200:
        return {"stored": False, "queued": False,
                "detail": explain_status(response.status_code, _detail(response))}
    try:
        return response.json()
    except Exception:  # noqa: BLE001
        return {"stored": False, "queued": False, "detail": "Unreadable answer from the backend."}


# --- one conversion -----------------------------------------------------------


@dataclass(frozen=True)
class Update:
    """One thing worth telling the person watching. Streamed, in order.

    Three kinds, because the app does three different things with them: `run`
    arrives once and is what a later 👎 attaches to, `node` is the progress
    line, and `state` is the whole graph state — the last one of those is the
    answer.
    """

    kind: str  # "run" | "node" | "state"
    node: str = ""
    run_id: str = ""
    state: dict[str, Any] = field(default_factory=dict)
    # What that node itself wrote, which for a single conversion is noise and
    # for a suite is the whole point: `convert_file` finishes twelve times and
    # each one carries the outcome of one file. Kept on `node` rather than
    # given a fourth kind, because it is the same event either way.
    update: dict[str, Any] = field(default_factory=dict)


def payload(source_text: str, source_path: str, *, context: dict[str, str] | None = None,
            refinement: str = "", max_attempts: int = 0) -> dict[str, Any]:
    """The request, with nothing in it the public demo would refuse.

    Text in, no paths: `source_path` travels as a *label* beside `source_text`,
    which is what makes the classifier, the recall query and the report able to
    say "LoginPage.ts" about a file the server has never seen. A companion goes
    the same way — `context_text`, a name to contents, never `context_paths`,
    which would be a request to open something on the server and is refused.

    `ask_risks` is False on purpose: the risk interrupt (step 7.2) is a real
    feature and it needs somebody to answer it. A web page that hangs
    mid-conversion waiting for a question nobody is going to ask is worse than
    not offering it.
    """
    request: dict[str, Any] = {
        "source_text": source_text,
        "source_path": source_path or "pasted.ts",
        "context_text": {k: v for k, v in (context or {}).items() if k.strip() and v.strip()},
        "refinement": refinement,
        "ask_risks": False,
    }
    if max_attempts:
        request["max_attempts"] = max_attempts
    return request


PLAIN_NAME_COMPLAINT = (
    "must be a plain name like LoginPage.ts — no folders, because the demo "
    "never reads files from the server"
)


def check_input(source_text: str, source_path: str, *,
                companion_name: str = "", companion_text: str = "") -> str:
    """Say what is wrong with this submission, or "" if nothing is.

    Everything here is checked again by the server. Checking it first is the
    difference between a sentence under the text box and a refusal after the
    button — the same rule, delivered at the moment it can still be fixed.
    """
    if not source_text.strip():
        return "Paste a TypeScript Selenium file, or press one of the sample buttons."
    size = len(source_text.encode("utf-8"))
    if size > MAX_BYTES:
        return f"That is {size // 1024} KB. The demo takes files up to {MAX_BYTES // 1024} KB."
    if source_path and not BARE_NAME.match(source_path):
        return f"The file name {PLAIN_NAME_COMPLAINT}."
    if companion_text.strip() and not companion_name.strip():
        # The name is not a label here, it is the import target: the converted
        # spec will `import { LoginPage } from "./LoginPage"`, and a companion
        # with no name is a file the new code cannot refer to.
        return "Give the companion a file name, so the converted file can import it."
    if companion_name and not BARE_NAME.match(companion_name):
        return f"The companion's name {PLAIN_NAME_COMPLAINT}."
    return ""


def stream(client_, thread_id: str | None, request: dict[str, Any],
           assistant: str = "convert", config: dict[str, Any] | None = None,
           context: dict[str, Any] | None = None) -> Iterator[Update]:
    """Run the graph and yield progress as it happens.

    `stream_mode=["updates", "values"]` is two different views of the same run:
    `updates` names the node that just finished (the progress line), `values` is
    the whole state after it (the answer, once the last one arrives). The run id
    comes down first, in a `metadata` event, which is how a thumbs-down twenty
    seconds later knows which run it is about.

    `config` and `context` are omitted entirely unless given, rather than sent
    as `None`: a single conversion wants the server's own defaults, and a suite
    cannot run without `max_concurrency` and a `recursion_limit` that grows with
    the wave count.
    """
    extra: dict[str, Any] = {}
    if config:
        extra["config"] = config
    if context:
        extra["context"] = context
    for chunk in client_.runs.stream(
        thread_id, assistant, input=request, stream_mode=["updates", "values"], **extra
    ):
        event, data = getattr(chunk, "event", ""), getattr(chunk, "data", None)
        if event and event.startswith("metadata") and isinstance(data, dict):
            run_id = str(data.get("run_id") or "")
            if run_id:
                yield Update("run", run_id=run_id)
        elif event and event.startswith("updates") and isinstance(data, dict):
            for node, written in data.items():
                yield Update("node", node=node,
                             update=written if isinstance(written, dict) else {})
        elif event and event.startswith("values") and isinstance(data, dict):
            yield Update("state", state=data)


def progress_label(node: str, seen: list[str]) -> str:
    """What to show for a node, counting laps when the loop goes round again.

    `seen` is every node already reported, this node included. The reflection
    loop revisits convert/validate/critic, and "Converting to Playwright" three
    times in a row looks like a stuck progress bar rather than the thing this
    project is actually about — so from the second lap on, the label says which
    attempt it is.
    """
    label = NODE_LABELS.get(node, node)
    if node in {"convert", "validate", "critic"}:
        lap = seen.count(node)
        if lap > 1:
            return f"{label} — attempt {lap}"
    return label


# --- reading the answer -------------------------------------------------------


@dataclass(frozen=True)
class Scorecard:
    """The final state, flattened into the things the page actually shows."""

    status: str  # passed | needs-review | refused | unavailable
    attempts: int
    reason: str
    gates: list[tuple[str, bool]] = field(default_factory=list)
    critic: str = "unavailable"
    code: str = ""
    todos: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    models: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    @property
    def gates_line(self) -> str:
        passed = sum(1 for _, ok in self.gates if ok)
        return f"{passed}/{len(self.gates)} gates" if self.gates else "no gates ran"


def scorecard(state: dict[str, Any]) -> Scorecard:
    """Read the graph's final state the way the page wants to show it.

    A refusal is a first-class outcome, not an error: an unsupported file (a
    Java test, a README, something 300 KB long) leaves through the `refuse` node
    with `report` still None and an honest sentence in `refusal`. Rendering that
    as "something went wrong" would be a lie about the one path the agent is
    most sure of.
    """
    report = state.get("report")
    if not isinstance(report, dict):
        refusal = str(state.get("refusal") or "")
        return Scorecard(
            status="refused" if refusal else "unavailable",
            attempts=int(state.get("iteration") or 0),
            reason=refusal or "The run ended without a report.",
            models=dict(state.get("models") or {}),
        )
    result = report.get("result") or {}
    critique = report.get("critique") or {}
    return Scorecard(
        status=str(report.get("status") or "unavailable"),
        attempts=int(report.get("attempts") or 0),
        reason=str(report.get("reason") or ""),
        gates=[(str(v.get("gate")), bool(v.get("passed")))
               for v in report.get("validation") or []],
        critic=str(critique.get("verdict") or "unavailable"),
        code=str(result.get("code") or ""),
        todos=[str(t) for t in result.get("todos") or []],
        notes=[str(n) for n in result.get("notes") or []],
        models=dict(state.get("models") or {}),
        errors=[str(e) for e in report.get("errors") or []],
    )


def unified_diff(before: str, after: str, before_name: str, after_name: str) -> str:
    """Selenium on the left, Playwright on the right, in `diff` syntax.

    A conversion is not an edit, so most lines are changed and the diff is long.
    It is still the view that answers the question people actually have — *what
    happened to my code* — and Streamlit colours `language="diff"` for free.
    """
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=before_name,
            tofile=after_name,
            n=3,
        )
    )


def download_name(source_path: str) -> str:
    """What the Download button calls the file.

    The same name it went in with: a converted `LoginPage.ts` is still the
    project's `LoginPage.ts`, and a converted `login.spec.ts` is still the spec —
    renaming them would break the imports between the two files this tool is
    most often asked to convert together.
    """
    name = (source_path or "pasted.ts").strip() or "pasted.ts"
    return name if name.endswith(".ts") else f"{name}.ts"


# --- the whole suite ----------------------------------------------------------
#
# Everything above this line is one file, pasted into a box, sent as *text*. The
# suite graph started out unable to be: its inputs were `root` and `out_root`,
# it walks the folder with `Path.rglob`, copies support files with
# `shutil.copyfile`, and writes a tree plus a markdown report to disk. Those are
# directories on whatever machine the graph runs on, which on a public host is
# not the visitor's machine — and `guard.py` refuses both fields by name,
# correctly, because `root: "/"` is a request to read the server.
#
# The way out was not to rewrite the graph. `source_tree` is the same folder as
# **text** — relative path to contents, the keys the manifest already uses — and
# `plan` materializes it into a temp directory the caller never names, so every
# node after that is step 9.2 and 9.3 unchanged: a real folder, a real `tsc`, a
# real tree. `finish` reads the result back out as text and deletes the
# workspace. Nothing in the request names a path on the server, which is the
# property the guard actually cares about.
#
# That leaves exactly one thing local-only, and it is an *input*, not a feature:
# naming a folder by path. `folder_blocker()` gates that. Uploading works
# against anything.
#
# The meter had to move too. It counted runs, and a twelve-file suite is one run
# and twelve conversions — so `limits.spend(runs=n)` charges per file, or one
# request would spend twelve times its share of a budget everybody shares.

# Hosts that mean "this machine". A backend on any of them shares a filesystem
# with the Streamlit process, which is the precondition for naming a folder.
LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"}

# The real thing, converted and committed: 12 files, 83.1s, 9 passed /
# 3 needs-review, tree compiles. Shown by the tab when it cannot run one itself,
# because "it does folders too, take my word for it" is not an answer.
SUITE_REPORT = env.REPO_ROOT / "docs" / "phase-9.3-report.md"

# What `--recall` means when the box is ticked. `store.DEFAULT_USER`, restated
# rather than imported, so the UI does not drag the store (and its embeddings)
# into a page that may never open a suite.
DEFAULT_USER = "local"


def is_local(url: str = "") -> bool:
    """Is the backend running on the same machine as this app?"""
    host = urlsplit(url or backend_url()).hostname or ""
    return host.lower() in LOCAL_HOSTS


def folder_blocker(url: str = "") -> str:
    """Why the *folder path* input is unavailable here, or "" when it is.

    Note what this is no longer about. Suites themselves work everywhere now:
    `source_tree` carries a folder as text, the graph materializes it into a
    directory it chooses, and nothing in the request names a path on the server.
    What stays local-only is pointing at a folder **by name** — `root` and
    `out_root` are still refused by `guard.py`, and still should be, because on
    a public host `root: "/"` is a request to read the machine.

    So this gates one input, not a feature: upload works against anything, and
    typing a path works against your own laptop.
    """
    if is_local(url):
        return ""
    return (
        f"Converting a folder **by path** needs a backend on this machine, and "
        f"this page is pointed at {url or backend_url()}. `root` and `out_root` "
        "name directories on the machine the graph runs on, so `guard.py` "
        "refuses them for anyone but the owner. Upload the files instead — that "
        "goes as text and works anywhere."
    )


def suite_key(url: str = "") -> str:
    """The key a suite run calls with, and it is not the same key both ways.

    **Locally, the owner's.** `guard.guard_run` starts with `if _is_owner(ctx):
    return True` and refuses `root`/`out_root` for everybody else, so a local
    backend started with auth on and both keys set would hand this page a *demo*
    identity and then refuse the folder it just offered — the worst of both. It
    is defensible only because `folder_blocker()` has already established the
    backend is this machine, so "the owner" and "the person sitting here" are
    the same person and the folders are already theirs.

    **Remotely, the demo key — deliberately, and this is the important half.**
    An uploaded suite is metered per file, and the meter is only reached on the
    demo path: a page that sent `S2P_API_KEY` to a public deployment would spend
    the whole day's budget past every guardrail step 10.4 built. So the owner
    key is scoped to the one case that needs it and cannot leak into the one
    that must not have it.

    Worth saying out loud rather than hiding: with a local backend this page can
    convert any folder the user running it can read. Bind Streamlit to
    `--server.address 127.0.0.1` and it is exactly as reachable as a terminal on
    the same machine, which is what `s2p suite` is anyway.
    """
    if not is_local(url):
        return demo_key()
    return (os.environ.get("S2P_API_KEY") or os.environ.get("S2P_DEMO_KEY") or "").strip()


def suite_client(url: str = "", key: str = "", visitor: str = ""):
    """A client for suite runs. Same SDK; the key and the header follow the mode.

    The visitor header is what the per-visitor meter counts. A local run does
    not need it — nothing is metered there — but an uploaded one does, and
    sending it in both cases is one fewer thing to get wrong.
    """
    return get_sync_client(url=url or backend_url(), api_key=key or suite_key(url),
                           headers={"X-S2P-Visitor": visitor} if visitor else None,
                           timeout=TIMEOUT)


# --- saying what a suite run is doing ----------------------------------------
#
# A suite run is minutes long and mostly silent, and the four node names it goes
# through are the graph's vocabulary, not a person's. These four helpers turn a
# `suite.census` into the sentence somebody watching would have written: what
# was found, and what is being converted right now.


def _plural(count: int, noun: str, many: str = "") -> str:
    """`1 page object`, `6 page objects`. The `many` form is for irregulars."""
    return f"{count} {noun if count == 1 else (many or noun + 's')}"


def _join(parts: list[str]) -> str:
    """`a`, `a and b`, `a, b and c` — the way a person lists things."""
    if len(parts) < 2:
        return parts[0] if parts else ""
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _found_parts(counts: dict[str, int], test_noun: str = "Selenium test file") -> list[str]:
    """The two things worth naming in a census, in the order they convert.

    Page objects first because that is wave 1, and the test count in brackets
    because it is the number that says how big this suite really is — six test
    files is a shrug, six test files holding forty tests is an afternoon of work
    somebody is about to not do by hand.

    A count of zero is left out rather than printed as "0 page objects": the
    sentence is about what is there.
    """
    parts = []
    if counts.get("page_objects"):
        parts.append(_plural(counts["page_objects"], "page object"))
    if counts.get("tests"):
        part = _plural(counts["tests"], test_noun)
        if counts.get("cases"):
            part += f" ({_plural(counts['cases'], 'test')})"
        parts.append(part)
    return parts


def wave_label(plan: "SuitePlan", number: int) -> str:
    """What wave `number` is converting, or "" when there is no such wave.

    The empty string is the important half. `next_wave` is a loop counter, so
    the graph runs it once per wave *and once more* — the run that finds nothing
    left and routes to `finish`. The old fixed label announced a wave that was
    never going to start, right before the report appeared. Returning "" here
    means the page simply does not show a line for it.
    """
    total = len(plan.waves)
    if not 1 <= number <= total:
        return ""
    counts = plan.wave_counts[number - 1] if number <= len(plan.wave_counts) else {}
    what = _join(_found_parts(counts, "test file")) or _plural(len(plan.waves[number - 1]), "file")
    where = f"Wave {number} of {total} · " if total > 1 else ""
    return f"{where}converting {what} to Playwright"


# The suite graph's nodes, in the same voice. `next_wave` is missing on purpose:
# it is the only node whose honest label depends on what is in the wave, so it
# is built by `wave_label` from the plan rather than looked up here. A fixed
# "Starting the next wave" said nothing — it fired between every wave and again
# after the last one, so a person watching saw the same six words three times
# and learned nothing about what the agent was doing.
SUITE_NODE_LABELS = {
    "plan": "Reading the folder and working out what converts before what",
    "convert_file": "Converting",
    "finish": "Compiling the converted tree as one project and writing the report",
}


@dataclass(frozen=True)
class SuitePlan:
    """What a folder looks like before a single model has been called.

    `s2p scan` in a dataclass. It is worth showing before the button because a
    suite run is the one thing here that costs twelve conversions instead of
    one, and "3 waves, 6 files, 4 copied across untouched" is the sentence that
    tells somebody whether they meant to press it.
    """

    root: str
    waves: list[list[str]]  # only the files that will actually be converted
    convert: list[str]
    copied: list[str]
    skipped: list[tuple[str, str]]  # (path, why)
    notes: list[str] = field(default_factory=list)
    # What the guard will charge for an uploaded tree: the files that will be
    # converted, after `--only`. Copied helpers are free, because they cost
    # nothing. The guard counts with `suite.conversions`, and so does this, so
    # the number on screen is the number the meter takes.
    billable: int = 0
    # What the files ARE, not just how many. `census` for the whole selection,
    # and one `census` per wave in the same order as `waves` — which is what
    # lets the progress line say "converting 6 page objects" instead of
    # "starting the next wave". Both are counted from the same manifest the
    # waves come from, so they cannot disagree with each other.
    counts: dict[str, int] = field(default_factory=dict)
    wave_counts: list[dict[str, int]] = field(default_factory=list)

    @property
    def files(self) -> int:
        return len(self.convert)

    @property
    def line(self) -> str:
        return (f"{self.files} file(s) to convert in {len(self.waves)} wave(s) · "
                f"{len(self.copied)} copied across · {len(self.skipped)} skipped")

    @property
    def found(self) -> str:
        """What the scan found, in the words the suite was written in.

        The sentence a person reads before they press the button, and the first
        thing the run says when they do. `line` is the arithmetic — files,
        waves, cost — and it stays, because that is what the budget is about.
        This is the shape: page objects, test files, and how many tests are
        actually inside them.
        """
        return "Found " + _join(_found_parts(self.counts) or ["nothing to convert"])

    @property
    def wave_lines(self) -> list[str]:
        """One short description per wave, for the list beside the button.

        The same words `wave_label` uses while the run is going, so the plan a
        person read before pressing and the line they watch afterwards are
        recognisably about the same wave.
        """
        return [_join(_found_parts(counts, "test file")) or _plural(len(wave), "file")
                for counts, wave in zip(self.wave_counts, self.waves)]


def plan_suite(root: str, only: list[str] | None = None) -> SuitePlan:
    """Scan the folder here, with no model and no server.

    The graph would scan it again anyway (`plan` does `state.get("manifest") or
    suite.scan(root)`), so this costs one directory walk and buys the wave plan
    on screen *before* the run. It is only correct because this path is local:
    the folder this process can see is the folder the graph will open.
    """
    return _plan_from(suite.scan(Path(root)), str(root), only)


def plan_tree(tree: dict[str, str], only: list[str] | None = None) -> SuitePlan:
    """The same preview for an uploaded tree, which is not on disk anywhere.

    `suite.scan` needs a directory, so this makes a throwaway one, scans it and
    deletes it. That is cheap (no model, no network) and it is worth doing even
    when the backend is remote and will materialize its own copy: the file count
    on screen is what a visitor is about to be charged for, and showing it
    before the button is the difference between a meter and a surprise.
    """
    with TemporaryDirectory(prefix="s2p-plan-") as tmp:
        root = suite.materialize(tree, Path(tmp) / "src")
        return _plan_from(suite.scan(root), "uploaded files", only)


def _plan_from(manifest, root: str, only: list[str] | None) -> SuitePlan:
    patterns = list(only or [])
    chosen = [f.path for f in manifest.convertible if suite.selected(f.path, patterns)]
    keep = set(chosen)
    by_path = {f.path: f for f in manifest.files}
    waves = [[p for p in wave if p in keep] for wave in manifest.waves
             if any(p in keep for p in wave)]
    return SuitePlan(
        root=str(root),
        billable=len(chosen),
        waves=waves,
        convert=chosen,
        copied=[f.path for f in manifest.files if f.action == suite.COPY],
        skipped=[(f.path, f.reason) for f in manifest.files if f.action == suite.SKIP],
        notes=list(manifest.notes),
        # Counted over `chosen`, not over the manifest: `--only` is a filter on
        # what will be converted, so a plan that charges for four files must not
        # claim to have found twelve.
        counts=suite.census(by_path[p] for p in chosen),
        wave_counts=[suite.census(by_path[p] for p in wave) for wave in waves],
    )


def check_suite(root: str, out_root: str, only: list[str] | None = None) -> str:
    """Say what is wrong with this suite run, or "" if nothing is.

    The same four refusals `s2p suite` makes before it builds a model, in the
    same order, for the same reason: the cheapest moment to say "--out is inside
    the folder you are converting" is while somebody is still typing it.
    """
    if not str(root).strip():
        return "Give the folder to convert, e.g. samples/selenium-suite."
    source, target = Path(root).expanduser(), Path(out_root or "").expanduser()
    if not source.is_dir():
        return f"{source} is not a folder on this machine."
    if not str(out_root).strip():
        return "Give an output folder. It is created if it does not exist."
    if target.resolve() == source.resolve():
        return "The output folder must be different from the suite being converted."
    if source.resolve() in target.resolve().parents:
        return "The output folder must not be inside the suite being converted."
    plan = plan_suite(str(source), only)
    if not plan.convert:
        return (f"Nothing in {source} can be converted"
                + (" with that --only filter." if only else
                   " — no TypeScript Selenium files found."))
    return ""


def affordable(snapshot: dict[str, Any], files: int) -> str:
    """Why this suite cannot be paid for, or "" when it can.

    Metering is per file now, so a suite has a price before it has a result and
    the page can say it in advance. Two ceilings, and they fail differently:

    * the **per-visitor daily cap** is a hard shape — a suite bigger than it can
      never run here, today or tomorrow, so the advice is to convert fewer files
      at a time rather than to come back later;
    * the **shared budget** is a number that refills at midnight UTC.

    What this deliberately does not do is guess how much of their own allowance
    the visitor has already spent. `/limits` reports the caps and the *global*
    usage, not one visitor's, so a page that subtracted anyway would be
    inventing a number. The guard is still the thing that knows; this only
    catches what is knowable before the click, which is most of it.
    """
    if files <= 0:
        return ""
    per = snapshot.get("per_visitor") or {}
    cap = per.get("daily")
    if isinstance(cap, int) and files > cap:
        return (f"This suite needs {files} conversions and the demo allows {cap} "
                f"conversions per visitor per day. Convert fewer at a time with “Only these "
                f"files”, or run it against your own machine.")
    budget = snapshot.get("budget") or {}
    remaining = budget.get("remaining")
    if isinstance(remaining, int) and files > remaining:
        return (f"This suite needs {files} conversions and {remaining} are left in "
                "today's shared budget. It resets at midnight UTC.")
    return ""


def suite_payload(root: str = "", out_root: str = "", only: list[str] | None = None,
                  report_path: str = "", tree: dict[str, str] | None = None) -> dict[str, Any]:
    """`SuiteState`'s inputs, in whichever of the two shapes the caller has.

    **Paths** (`root`/`out_root`) when the backend is this machine: the folder is
    already here, and sending its contents to a server that can open it would be
    posting somebody their own filesystem.

    **Text** (`source_tree`) when it is not: relative path to file text, the same
    keys the manifest already uses. The graph writes it into a temp directory it
    chooses, converts it exactly as it converts a folder, and sends the finished
    tree back as text. No path in this request names anything on the server,
    which is what `guard.py` requires and what makes the public demo able to do
    suites at all.

    `manifest` is deliberately *not* sent even though the page just built one: it
    is a frozen dataclass full of other frozen dataclasses, and putting it
    through JSON to save the graph one directory walk would be trading a real
    serialisation problem for an imaginary performance one.
    """
    request: dict[str, Any] = {
        "only": [p for p in (only or []) if p.strip()],
        "report_path": str(report_path or ""),
    }
    if tree:
        request["source_tree"] = dict(tree)
        return request
    request["root"] = str(Path(root).expanduser())
    request["out_root"] = str(Path(out_root).expanduser())
    return request


def suite_context(model: str = "", critic_model: str = "", max_attempts: int = 0,
                  user_id: str = "") -> dict[str, Any]:
    """The run's context — models and the lap cap, resolved by the server.

    A dict rather than a `SuiteSettings`, because it travels as JSON;
    `suite_graph.settings()` already accepts either and rebuilds the dataclass
    on the far side. Empty strings are left out so the server's own defaults
    win, which is what makes "leave it alone" a real choice on the form.
    """
    context: dict[str, Any] = {}
    if model.strip():
        context["model"] = model.strip()
    if critic_model.strip():
        context["critic_model"] = critic_model.strip()
    if max_attempts:
        context["max_attempts"] = int(max_attempts)
    # "" is meaningful: it is how the CLI spells --no-recall, so it is sent.
    context["user_id"] = user_id
    return context


def suite_config(waves: int, parallel: int = 4) -> dict[str, Any]:
    """The two limits that keep a many-file run inside its bounds.

    `cli.suite_run_config` restated without the trace tags, and pinned against
    it by a test. `max_concurrency` is what actually caps the fan-out — without
    it LangGraph starts every `Send` in a wave at once, so a forty-file wave
    opens forty connections to the provider. `recursion_limit` counts
    super-steps and each wave costs two (dispatch, then the join), so it has to
    grow with the suite rather than be a number that works until it does not.
    """
    return {"max_concurrency": max(1, int(parallel)),
            "recursion_limit": 2 * max(1, int(waves)) + 6}


def _get(obj: Any, name: str, default: Any = None) -> Any:
    """Read a field off a dataclass or off the dict it turns into over HTTP.

    Everything the graph returns arrives here as JSON: `FileOutcome` is a dict,
    `Assembly` is a dict, and their tuples are lists. In a test that invokes the
    graph directly they are still dataclasses. One accessor rather than a
    conversion layer, because the alternative is two readers that drift.
    """
    if isinstance(obj, dict):
        return obj.get(name, default)
    got = getattr(obj, name, default)
    return default if got is None else got


@dataclass(frozen=True)
class FileRow:
    """One file's line in the table. `suite_graph.FileOutcome`, flattened."""

    path: str
    wave: int
    status: str  # passed | needs-review | refused | failed
    attempts: int = 0
    reason: str = ""
    gates: list[tuple[str, bool]] = field(default_factory=list)
    critic: str = ""
    todos: list[str] = field(default_factory=list)
    seconds: float = 0.0
    written: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "passed"

    @property
    def gates_line(self) -> str:
        if not self.gates:
            return "—"
        return f"{sum(1 for _, ok in self.gates if ok)}/{len(self.gates)}"


def file_row(outcome: Any) -> FileRow:
    """One `FileOutcome`, however it arrived, as a row."""
    gates = []
    for pair in _get(outcome, "gates", []) or []:
        name, ok = (pair[0], pair[1]) if not isinstance(pair, dict) else (
            pair.get("gate"), pair.get("passed"))
        gates.append((str(name), bool(ok)))
    return FileRow(
        path=str(_get(outcome, "path", "")),
        wave=int(_get(outcome, "wave", 0) or 0),
        status=str(_get(outcome, "status", "failed")),
        attempts=int(_get(outcome, "attempts", 0) or 0),
        reason=str(_get(outcome, "reason", "")),
        gates=gates,
        critic=str(_get(outcome, "critic", "")),
        todos=[str(t) for t in _get(outcome, "todos", []) or []],
        seconds=float(_get(outcome, "seconds", 0.0) or 0.0),
        written=str(_get(outcome, "written", "")),
        errors=[str(e) for e in _get(outcome, "errors", []) or []],
    )


def rows_in(update: dict[str, Any]) -> list[FileRow]:
    """The files a single `convert_file` update just finished.

    This is the live half of the suite tab. The fan-out means `convert_file`
    completes once per file, in whatever order the files finish, and each of
    those updates carries exactly the one outcome that branch appended — so a
    page watching the `updates` stream can tick files off as they land instead
    of showing a spinner for eighty seconds.
    """
    return [file_row(o) for o in update.get("outcomes") or []]


def suite_totals(rows: list[FileRow]) -> dict[str, int]:
    """How many of each status. Same four buckets `suite_graph.totals` uses."""
    counts = {"passed": 0, "needs-review": 0, "refused": 0, "failed": 0}
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
    return counts


@dataclass(frozen=True)
class SuiteResult:
    """A finished suite run, flattened into the things the page shows.

    The per-file rows are step 9.2. Everything from `compiles` down is step
    9.3's assembly, and it is the half worth reading: every per-file gate
    verdict is a *local* claim — this file compiled against the companions it
    happened to import — so a page object whose method two specs call
    differently passes twelve green rows and still leaves a folder that does not
    build. `compiles` is the answer to the question the rows cannot answer.
    """

    rows: list[FileRow] = field(default_factory=list)
    elapsed: float = 0.0
    compiles: bool = False
    tree_files: int = 0
    tree_findings: list[str] = field(default_factory=list)
    tree_error: str = ""
    kept: int = 0
    renamed: int = 0
    removed: int = 0
    unexplained: int = 0
    losses: list[tuple[str, str, str, str]] = field(default_factory=list)
    todos: list[tuple[str, list[str]]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    report_path: str = ""
    markdown: str = ""
    assembled: bool = False
    # The converted files as text, for a run that sent its suite as text. Empty
    # for a folder run, where they are already on the caller's disk — sending
    # somebody their own files back would be noise, and the folder path on
    # screen is the better answer.
    tree: dict[str, str] = field(default_factory=dict)

    @property
    def totals(self) -> dict[str, int]:
        return suite_totals(self.rows)

    @property
    def passed(self) -> bool:
        """Exit 0's rule, restated: every file green *and* the tree builds.

        A compile that could not be run is not a pass. `assemble.Assembly`
        makes the same choice for the same reason — unknown is never green.
        """
        counts = self.totals
        return bool(self.rows) and self.compiles and counts["passed"] == len(self.rows)

    @property
    def headline(self) -> str:
        counts = self.totals
        parts = [f"{n} {name}" for name, n in counts.items() if n]
        tree = "tree compiles" if self.compiles else (
            "tree does not compile" if self.assembled else "tree not compiled")
        return " · ".join([*parts, tree, f"{self.elapsed:.1f}s"])


def suite_result(state: dict[str, Any]) -> SuiteResult:
    """Read the suite graph's final state the way the page wants to show it.

    Written to survive a half-finished run: a suite that fell over in wave 2 has
    outcomes and no assembly, and the rows it did produce are still the most
    useful thing on the screen. So the assembly is read defensively and
    `assembled` says whether there was one, rather than every number quietly
    reading zero.
    """
    rows = sorted((file_row(o) for o in state.get("outcomes") or []),
                  key=lambda r: (r.wave, r.path))
    converted = {str(k): str(v) for k, v in (state.get("converted_tree") or {}).items()}
    assembly = state.get("assembly")
    if assembly is None:
        return SuiteResult(rows=rows, tree=converted,
                           elapsed=float(state.get("elapsed") or 0.0))

    tree = _get(assembly, "tree")
    findings = [
        f"{_get(f, 'file', '')}"
        + (f":{_get(f, 'line')}" if _get(f, "line") else "")
        + f" {_get(f, 'code', '')} {_get(f, 'message', '')}"
        for f in (_get(tree, "findings", []) or [])
    ] if tree else []

    kept = renamed = removed = unexplained = 0
    losses: list[tuple[str, str, str, str]] = []
    for ledger in _get(assembly, "ledgers", []) or []:
        path = str(_get(ledger, "path", ""))
        for change in _get(ledger, "changes", []) or []:
            verdict = str(_get(change, "verdict", ""))
            if verdict == "kept":
                kept += 1
                continue
            name = str(_get(change, "name", ""))
            reason = str(_get(change, "reason", ""))
            if verdict == "renamed":
                renamed += 1
                losses.append((path, name, f"renamed to {_get(change, 'counterpart', '')}", reason))
            elif verdict == "removed":
                removed += 1
                unexplained += 0 if reason else 1
                losses.append((path, name, "removed", reason or "no reason given"))

    return SuiteResult(
        rows=rows,
        elapsed=float(state.get("elapsed") or 0.0),
        compiles=bool(tree is not None and _get(tree, "passed", False)),
        tree_files=int(_get(assembly, "files", 0) or 0),
        tree_findings=findings,
        tree_error=str(_get(assembly, "tree_error", "")),
        kept=kept, renamed=renamed, removed=removed, unexplained=unexplained,
        losses=losses,
        todos=[(str(_get(t, "text", "")), [str(p) for p in _get(t, "places", []) or []])
               for t in _get(assembly, "todos", []) or []],
        notes=[str(n) for n in _get(assembly, "notes", []) or []],
        report_path=str(_get(assembly, "report_path", "")),
        markdown=str(_get(assembly, "markdown", "")),
        assembled=True,
        tree=converted,
    )


# --- uploads, in and out ------------------------------------------------------
#
# A paste box is the right input for "show me what this does to one file" and
# the wrong one for everything else: nobody pastes twelve files. These are the
# two directions — bytes a browser hands us, and bytes we hand back — and they
# are here rather than in `ui/app.py` for the usual reason: decoding a zip is a
# decision, and decisions are tested.


def read_upload(upload) -> tuple[str, str]:
    """One uploaded file as (name, text). Raises `ValueError` if it is not text.

    Takes anything with `.name` and `.getvalue()`, which is Streamlit's
    `UploadedFile` and also a `SimpleNamespace` in a test — the page should not
    be the only thing that can drive this.

    Decoded strictly rather than with `errors="replace"`: a file that is not
    UTF-8 text is not a TypeScript file, and quietly turning its bytes into
    question marks would send the model a corrupted source and blame the answer.
    """
    name = getattr(upload, "name", "") or ""
    raw = upload.getvalue() if hasattr(upload, "getvalue") else upload.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"{name or 'That file'} is not UTF-8 text — this converts TypeScript "
            "source, not compiled or binary files."
        ) from exc
    # A BOM is invisible in an editor and is a syntax error to `tsc`, which
    # would come back as a compile-gate finding about the visitor's own file.
    return name, text.lstrip("﻿")


# Junk that rides along in real zips. `node_modules` is the one that matters —
# a suite zipped with its dependencies is tens of thousands of files, and every
# one of them would be charged for.
_ZIP_JUNK_DIRS = frozenset({"__MACOSX", "node_modules", ".git", "dist", "build"})


def tree_from_zip(data: bytes) -> dict[str, str]:
    """Every source file in a zip, keyed by its path with the wrapper stripped.

    Two things this does beyond unzipping, both because of what real zips look
    like:

    **The common prefix goes.** Zipping a folder gives you
    `selenium-suite/pages/LoginPage.ts`, and that leading directory is not part
    of anybody's import paths. Keeping it would not break the conversion — the
    imports are relative — but it would put a meaningless folder in every path
    on screen and in the report.

    **Junk is dropped rather than refused.** `__MACOSX`, `.DS_Store` and
    `node_modules` are things a zip has, not things a person meant to send;
    refusing the upload over them would be correct and useless.
    """
    found: dict[str, str] = {}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            parts = Path(info.filename).parts
            if any(part in _ZIP_JUNK_DIRS or part.startswith(".") for part in parts):
                continue
            if Path(info.filename).suffix.lower() not in suite.SOURCE_SUFFIXES:
                continue
            if info.file_size > suite.MAX_TREE_BYTES:
                raise ValueError(f"{info.filename} is larger than this accepts on its own.")
            try:
                found[Path(info.filename).as_posix()] = (
                    archive.read(info).decode("utf-8").lstrip("﻿"))
            except UnicodeDecodeError as exc:
                raise ValueError(f"{info.filename} is not UTF-8 text.") from exc
    return _unwrap(found)


def _unwrap(tree: dict[str, str]) -> dict[str, str]:
    """Drop a single wrapping directory shared by every path, if there is one."""
    if len(tree) < 2:
        # With one file there is no evidence of a wrapper, and stripping the
        # only directory would turn `pages/LoginPage.ts` into `LoginPage.ts`.
        return tree
    firsts = {Path(name).parts[0] for name in tree if len(Path(name).parts) > 1}
    if len(firsts) != 1 or any(len(Path(name).parts) == 1 for name in tree):
        return tree
    prefix = firsts.pop()
    return {Path(name).relative_to(prefix).as_posix(): text for name, text in tree.items()}


def tree_from_uploads(uploads: list) -> tuple[dict[str, str], str]:
    """Everything the visitor dropped, as one tree, plus a complaint or "".

    Zips are expanded and plain files are taken as they are, so "drag the
    folder's files in" and "drag a zip of the folder in" are the same gesture
    with the same result. Loose files keep only their base name, because a
    browser does not send the directory a file came from — which is exactly why
    the zip route exists and is the one to prefer for a suite with folders.
    """
    tree: dict[str, str] = {}
    for upload in uploads or []:
        name = getattr(upload, "name", "") or ""
        try:
            if name.lower().endswith(".zip"):
                data = upload.getvalue() if hasattr(upload, "getvalue") else upload.read()
                tree.update(tree_from_zip(data))
            else:
                _, text = read_upload(upload)
                tree[Path(name).name] = text
        except ValueError as exc:
            return {}, str(exc)
    if not tree:
        return {}, ""
    return tree, suite.check_tree(tree)


def converted_zip(files: dict[str, str], report: str = "") -> bytes:
    """The converted tree as a zip, which is what "give me my suite back" means.

    The report goes in beside the code rather than being a second button: it is
    the document that says which of these files still needs eyes, and the two
    parting company is how it gets ignored.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in sorted(files.items()):
            archive.writestr(name, text)
        if report:
            archive.writestr(assemble.REPORT_NAME, report)
    return buffer.getvalue()


# --- when it goes wrong -------------------------------------------------------


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except Exception:  # noqa: BLE001
        return response.text[:400]
    if isinstance(body, dict):
        for key in ("detail", "error", "message"):
            value = body.get(key)
            if isinstance(value, str) and value:
                return value
    return str(body)[:400]


def explain_status(status_code: int, detail: str = "") -> str:
    """Turn an HTTP failure into something a visitor can act on.

    Every one of these is a rule from step 10.4 arriving as a number. The
    server's own `detail` is usually the better sentence — it names the field it
    refused and why — so it is kept, and only the ones that need context get a
    line of ours in front.
    """
    if status_code == 401:
        return ("The backend refused this playground's key. Check S2P_DEMO_KEY "
                "matches the deployment. " + detail).strip()
    if status_code == 403:
        # The one 403 that is not a rule the visitor broke. A deployment built
        # before `source_tree` existed has no idea what one is, falls through to
        # the single-file check, and asks for `source_text` — which reads like a
        # refusal of the upload rather than what it is: an old image.
        if "source_text" in detail and "source_tree" not in detail:
            return ("This backend was built before suite uploads existed — it is "
                    "asking for a single file's text. Redeploy it (deploy/fly) to "
                    "convert whole suites here. " + detail).strip()
        return detail or "The public demo does not allow that."
    if status_code == 429:
        return detail or "The demo is at its limit for now. Try again shortly."
    if status_code == 503:
        return detail or "The backend is not accepting requests."
    if status_code >= 500:
        return f"The backend failed ({status_code}). {detail}".strip()
    return f"{status_code}: {detail}" if detail else f"The backend answered {status_code}."


def explain(exc: Exception) -> str:
    """The same, for whatever the SDK raises instead of returning a response."""
    response = getattr(exc, "response", None)
    if response is not None and getattr(response, "status_code", None):
        return explain_status(response.status_code, _detail(response))
    if isinstance(exc, httpx.TimeoutException):
        return ("The backend did not answer in time. A conversion can take a minute; "
                "if this keeps happening the deployment may be down.")
    if isinstance(exc, httpx.HTTPError):
        return (f"Could not reach the backend at {backend_url()} "
                f"({type(exc).__name__}). Is it running?")
    return f"{type(exc).__name__}: {exc}"


def recognized(exc: Exception) -> bool:
    """Whether `explain` has a sentence of its own for this, or falls back.

    Its last line is the exception's own text, which is written for whoever
    raised it and is free to carry a URL, a path, or a header. On this machine
    that is the most useful thing on the screen. On the public page it is a
    stranger's browser, so `ui/server.py` asks this first and logs the rest.

    Lives next to `explain` because it mirrors its branches — move one and the
    other has to move.
    """
    response = getattr(exc, "response", None)
    if response is not None and getattr(response, "status_code", None):
        return True
    # TimeoutException is an HTTPError, so this covers both of explain's cases.
    return isinstance(exc, httpx.HTTPError)


def retry_after(exc: Exception) -> int:
    """Seconds to wait, when the refusal was a rate limit that said so."""
    response = getattr(exc, "response", None)
    header = getattr(response, "headers", {}) or {}
    try:
        return int(header.get("Retry-After") or header.get("retry-after") or 0)
    except (TypeError, ValueError):
        return 0
