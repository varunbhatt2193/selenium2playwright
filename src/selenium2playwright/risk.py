"""Step 7.2 — the questions only a human can answer. No LLM, no network.

Some Selenium patterns have exactly one correct Playwright translation, and the
playbook already settles those. A few have *more than one*, and picking between
them is a judgement about the suite's intent, not about the code:

  dialogs        Selenium handles a browser dialog AFTER the click that opened
                 it. Playwright must register the handler BEFORE that click, and
                 auto-dismisses anything unhandled. Accept or dismiss changes
                 which branch of the application runs, so guessing can silently
                 test the wrong thing.
  javascript     `executeScript` is usually a workaround for something Selenium
                 could not do (scroll, click through an overlay, poll). Playwright
                 can often do it natively — but only the author knows whether the
                 script was a workaround or the thing under test.
  shared session A `before` hook that logs in once and lets later tests inherit
                 that session. Playwright gives every test a fresh context, so
                 that inheritance has to be rebuilt deliberately (a saved
                 storageState, a per-test login, or an explicitly serial suite).

This module finds those patterns and writes the question. The graph asks it with
`interrupt()` (graph.review); the answer becomes prompt text for the actor and
the critic. Detection is deliberately deterministic — regex over the source, the
same policy as classify.py — so the same file always raises the same questions,
which is what makes it safe for a node that LangGraph re-runs on every resume.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# How many lines after a `before(` hook count as "inside the hook". Long enough
# for a realistic setup block, short enough not to swallow the tests below it.
HOOK_BODY_LINES = 20


@dataclass(frozen=True)
class Risk:
    """One flagged pattern. Small on purpose: this goes into the checkpoint.

    The question text and answer options live in RISKS below, keyed by kind, so
    that improving the wording never invalidates a saved thread.
    """

    kind: str  # a key of RISKS
    line: int  # 1-based line of the first occurrence
    snippet: str  # that line, stripped
    count: int  # how many lines in the file match this kind


@dataclass(frozen=True)
class Option:
    """One answer the user can pick, and the sentence the model is then given."""

    key: str
    label: str  # short, for the menu
    guidance: str  # written to the model, imperative, one sentence or two


@dataclass(frozen=True)
class RiskKind:
    title: str
    why: str  # why this cannot be decided by rule — shown to the user
    question: str
    options: tuple[Option, ...]  # options[0] is the default

    @property
    def default(self) -> Option:
        return self.options[0]

    def option(self, key: str) -> Option | None:
        return next((o for o in self.options if o.key == key), None)


RISKS: dict[str, RiskKind] = {
    "dialogs": RiskKind(
        title="browser dialog handling",
        why=("Selenium handles a dialog after the click; Playwright must register the handler "
             "before it, and auto-dismisses anything unhandled. Accept and dismiss run different "
             "application branches, so this cannot be inferred from the code."),
        question="How should the converted code handle browser dialogs?",
        options=(
            Option("handler-first", "register page.once('dialog', ...) before the action (recommended)",
                   "Handle browser dialogs by registering page.once(\"dialog\", ...) immediately BEFORE "
                   "the action that opens the dialog, choosing accept() or dismiss() to match exactly what "
                   "the Selenium code did, and assert the dialog's message where the original did."),
            Option("expect-event", "await the dialog event alongside the action",
                   "Handle browser dialogs by awaiting the dialog event alongside the triggering action "
                   "(register the handler or waitForEvent(\"dialog\") first, then perform the action, then "
                   "await both), so the dialog object itself is available to assert on. Match the original "
                   "accept/dismiss choice exactly."),
            Option("auto-dismiss", "let Playwright auto-dismiss; assert only the page outcome",
                   "Do not register a dialog handler: rely on Playwright's default auto-dismiss and assert "
                   "only the resulting page state. Apply this ONLY where the Selenium code dismissed the "
                   "dialog; where it accepted one, keep an explicit handler and add a TODO(review) saying "
                   "auto-dismiss would have changed the branch under test."),
        ),
    ),
    "javascript-execution": RiskKind(
        title="executeScript / executeAsyncScript",
        why=("Injected JavaScript is usually a workaround for a Selenium limitation that Playwright "
             "does not have — but sometimes the script IS the thing under test. Replacing it changes "
             "what the test proves; keeping it can carry a workaround into a tool that never needed it."),
        question="What should happen to the injected JavaScript?",
        options=(
            Option("native-first", "replace workarounds with native Playwright actions (recommended)",
                   "Where the injected JavaScript is a Selenium workaround (scrolling into view, clicking "
                   "through an overlay, polling for a condition), replace it with the native Playwright "
                   "equivalent and note the replacement in the conversion notes. Keep page.evaluate() only "
                   "where no native equivalent exists, and add a TODO(review) for any script whose purpose "
                   "you cannot determine from the source."),
            Option("keep-evaluate", "port every script verbatim to page.evaluate()",
                   "Port every injected script verbatim to page.evaluate() with the same arguments and "
                   "return value. Do not substitute native Playwright actions, even where one exists: the "
                   "script's behaviour is part of what the test asserts."),
            Option("flag-only", "port verbatim and flag each one for a human",
                   "Port every injected script verbatim to page.evaluate() AND add a TODO(review) at each "
                   "one naming what it appears to do and which native Playwright action could replace it, "
                   "so a human can decide. Change no behaviour."),
        ),
    ),
    "shared-session": RiskKind(
        title="session shared across tests",
        why=("A one-time login in `before` works in Selenium because every test drives the same browser. "
             "Playwright gives each test a fresh, isolated context, so the login has to be rebuilt "
             "somewhere — and which way is right depends on whether these tests are truly independent."),
        question="Each Playwright test starts logged out. How should the shared session be rebuilt?",
        options=(
            Option("login-per-test", "log in in test.beforeEach — full isolation (recommended)",
                   "Rebuild the shared session by moving the login into test.beforeEach so every test "
                   "authenticates itself and is fully independent. Keep the assertions and the order of "
                   "the tests unchanged."),
            Option("storage-state", "save storageState once and reuse it",
                   "Rebuild the shared session by logging in once and saving the authenticated "
                   "storageState, then have the tests start from it (test.use({ storageState: ... })). "
                   "Anything that must change outside this file — a setup project or playwright.config "
                   "entry — must be stated in the conversion notes and marked TODO(review), not assumed."),
            Option("serial-shared", "keep one page and run the tests serially",
                   "Preserve the original order dependency: wrap the tests in test.describe.serial, create "
                   "one page in test.beforeAll, log in there, and share it. Add a TODO(review) noting the "
                   "suite now cannot run in parallel, exactly as the Selenium version could not."),
        ),
    ),
}

# One pattern per kind. Matched line by line so a finding can quote its evidence.
SIGNATURES: dict[str, str] = {
    "dialogs": (r"until\s*\.\s*alertIsPresent\s*\(|switchTo\s*\(\s*\)\s*\.\s*alert\s*\(|"
                r"\b\w*(?:alert|dialog|confirm|prompt)\w*\s*\.\s*(?:accept|dismiss|sendKeys)\s*\("),
    "javascript-execution": r"\bexecute(?:Async)?Script\s*\(",
    # The hook itself is not the risk; what the hook DOES is. See _shared_session.
    # Three ways a suite authenticates once: a login helper, state written
    # straight into the browser, or credentials typed into a login page.
    "shared-session": (r"\b(?:log_?in|sign_?in|authenticate|logon)\s*\(|"
                       r"\baddCookie\s*\(|\b(?:local|session)Storage\s*\.\s*setItem\s*\(|"
                       r"\b(?:username|password|credentials?)\b|['\"][^'\"]*/(?:login|sign-?in)\b"),
}

BEFORE_HOOK = re.compile(r"\bbefore(?:All)?\s*\(")
TEST_CASE = re.compile(r"\b(?:it|test)\s*\(")


def _matches(pattern: str, lines: list[str], within: range | None = None) -> list[int]:
    """1-based line numbers matching pattern, optionally restricted to `within`."""
    regex = re.compile(pattern, flags=re.IGNORECASE)
    return [n for n, line in enumerate(lines, 1)
            if regex.search(line) and (within is None or n in within)]


def _hook_body(lines: list[str], start: int) -> range:
    """The lines inside the block opened on line `start` (1-based).

    Ends at the first later line indented no further than the opening one — the
    hook's own `});`. Formatting decides the boundary, which is why the cap is
    still there: a minified or oddly indented file gets a bounded guess instead
    of the rest of the file.
    """
    opening = lines[start - 1]
    indent = len(opening) - len(opening.lstrip())
    limit = min(start + HOOK_BODY_LINES, len(lines))
    for number in range(start + 1, limit + 1):
        line = lines[number - 1]
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            return range(start + 1, number)
    return range(start + 1, limit + 1)


def _shared_session(lines: list[str]) -> list[int]:
    """Auth or state set up in a one-time `before` hook that later tests inherit.

    Two conditions, both needed: the file has at least two tests (one test cannot
    inherit anything from another), and a `before`/`beforeAll` hook contains a
    login, cookie, or storage write. `beforeEach` is not a risk — it converts to
    `test.beforeEach` with no decision to make — and neither is building the
    driver in `before`, which is the setup every Selenium suite has.
    """
    if len(_matches(TEST_CASE.pattern, lines)) < 2:
        return []
    hits: list[int] = []
    for start in _matches(BEFORE_HOOK.pattern, lines):
        hits.extend(_matches(SIGNATURES["shared-session"], lines, within=_hook_body(lines, start)))
    return sorted(set(hits))


def detect_risks(source: str) -> list[Risk]:
    """Every risky pattern in the file, at most one Risk per kind, in RISKS order.

    One question per kind, not per occurrence: the answer is a policy for the
    whole file, so asking twice about two dialogs would be asking the same thing
    twice. The count travels with the finding so the user knows the scale.
    """
    lines = source.splitlines()
    risks = []
    for kind in RISKS:
        hits = _shared_session(lines) if kind == "shared-session" else _matches(SIGNATURES[kind], lines)
        if hits:
            risks.append(Risk(kind=kind, line=hits[0], snippet=lines[hits[0] - 1].strip(), count=len(hits)))
    return risks


def question(risk: Risk) -> dict:
    """The payload handed to interrupt() — everything a human needs to answer.

    A plain dict, because it is serialized into the checkpoint and read back by
    whatever front end is asking: the CLI today, a web playground in Phase 11.
    """
    kind = RISKS[risk.kind]
    return {
        "kind": risk.kind,
        "title": kind.title,
        "question": kind.question,
        "why": kind.why,
        "evidence": f"line {risk.line}: {risk.snippet}"
                    + (f"  (+{risk.count - 1} more)" if risk.count > 1 else ""),
        "options": [{"key": o.key, "label": o.label} for o in kind.options],
        "default": kind.default.key,
    }


def resolve(kind_name: str, answer: str) -> str:
    """Turn one answer into the sentence the model is given.

    An empty answer means "use the default". An answer that names an option
    becomes that option's guidance. Anything else is the user's own words, kept
    verbatim — a menu should never be the only way to say what you want.
    """
    kind = RISKS[kind_name]
    answer = (answer or "").strip()
    if not answer:
        return kind.default.guidance
    option = kind.option(answer)
    return option.guidance if option is not None else answer


def decision_lines(risks: list[Risk], decisions: dict[str, str]) -> list[str]:
    """One rendered line per answered risk, in the order the risks were found."""
    return [f"{RISKS[r.kind].title} ({r.snippet}) — {resolve(r.kind, decisions[r.kind])}"
            for r in risks if r.kind in decisions]
