"""The door before the model: is this a Selenium file, and is it talking to the AI?

A pasted file is somebody's text, and every byte of it is read by a model. Two
things must never get that far on the single-file demo:

    not Selenium   an essay, a Java class, a poem with one import line on top —
                   the model would happily "convert" it, on our budget
    injection      text inside an otherwise real Selenium file that is addressed
                   to the model: "ignore the playbook", a fake </source_file>,
                   invisible characters hiding either

`classify()` already refuses a file with no `selenium-webdriver` import, but it
matches that import anywhere, comments included, and never reads anything else.
This module reads the file the way the compiler would — comments, strings and
code told apart — and answers with one sentence, or "" when it has nothing to
say. Same contract as `playground.check_input` and `suite.check_tree`, because
it is called beside them: the page's server before the request is made, the
guard before the meter is charged.

What this cannot do, said plainly: injection is open-ended English, so a
pattern list catches the known shapes — override phrases, role hijacks, chat
markers, our own delimiters, hidden Unicode, concealment requests — and not a
paraphrase nobody has written yet. It is the first of three layers, not the
only one: the prompt tells the model the file is data (playbook rule 29), and
the parity gate fails any module the conversion loads that the source did not.

Pure Python, no Node, on purpose: the playground's image carries no toolchain,
and this has to run there.
"""

from __future__ import annotations

import re
import unicodedata
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import PurePath

from selenium2playwright.classify import classify

# --- reading the file ---------------------------------------------------------


@dataclass(frozen=True)
class Token:
    kind: str  # comment | string | template | regex | word | number | punct
    text: str  # comments and strings without their delimiters
    line: int


class NotTypeScript(ValueError):
    def __init__(self, line: int, what: str):
        super().__init__(what)
        self.line, self.what = line, what


# After one of these a `/` starts a regular expression; after anything else
# (a name, a number, `)`, `]`, `}`) it divides. The rule every JS tokenizer uses.
REGEX_AFTER_WORDS = frozenset({"return", "typeof", "instanceof", "in", "of", "new", "delete",
                               "void", "throw", "case", "do", "else", "yield", "await"})

WORD = re.compile(r"[A-Za-z_$\u0080-\uffff][\w$\u0080-\uffff]*")
NUMBER = re.compile(r"\d[\w.]*|\.\d[\w]*")


def tokenize(source: str) -> list[Token]:
    """Comments, strings, template text, regex literals, words, numbers, punctuation.

    Not a parser — enough to know which characters are code and which are text a
    human wrote, and that is all the checks below need. Raises NotTypeScript for
    the shapes no TypeScript file can have: a string still open at the end of its
    line, a comment or template still open at the end of the file.
    """
    tokens: list[Token] = []
    i, line, n = 0, 1, len(source)
    # One entry per open template literal: the brace depth its `${` began at.
    templates: list[int] = []
    depth = 0

    def last_significant() -> Token | None:
        for token in reversed(tokens):
            if token.kind != "comment":
                return token
        return None

    def read_template(start: int, start_line: int) -> int:
        """Read template text from `start` up to its closing backtick or a `${`."""
        nonlocal line
        j, text = start, []
        while j < n:
            ch = source[j]
            if ch == "\\" and j + 1 < n:
                text.append(source[j + 1]); line += source[j + 1] == "\n"; j += 2
                continue
            if ch == "`":
                tokens.append(Token("template", "".join(text), start_line))
                return j + 1
            if ch == "$" and j + 1 < n and source[j + 1] == "{":
                tokens.append(Token("template", "".join(text), start_line))
                templates.append(depth)
                return j + 2
            line += ch == "\n"
            text.append(ch)
            j += 1
        raise NotTypeScript(start_line, "a template string is never closed")

    while i < n:
        ch = source[i]
        if ch == "\n":
            line += 1; i += 1
        elif ch.isspace():
            i += 1
        elif source.startswith("//", i):
            end = source.find("\n", i)
            end = n if end == -1 else end
            tokens.append(Token("comment", source[i + 2:end], line)); i = end
        elif source.startswith("/*", i):
            end = source.find("*/", i + 2)
            if end == -1:
                raise NotTypeScript(line, "a /* comment is never closed")
            body = source[i + 2:end]
            tokens.append(Token("comment", body, line)); line += body.count("\n"); i = end + 2
        elif ch in "'\"":
            j, text = i + 1, []
            while j < n and source[j] != ch:
                if source[j] == "\n":
                    raise NotTypeScript(line, "a string is still open at the end of its line")
                if source[j] == "\\" and j + 1 < n:
                    if source[j + 1] == "\n":
                        line += 1
                    text.append(source[j + 1]); j += 2
                    continue
                text.append(source[j]); j += 1
            if j >= n:
                raise NotTypeScript(line, "a string is never closed")
            tokens.append(Token("string", "".join(text), line)); i = j + 1
        elif ch == "`":
            i = read_template(i + 1, line)
        elif ch == "}" and templates and templates[-1] == depth:
            templates.pop()
            i = read_template(i + 1, line)
        elif ch == "/":
            previous = last_significant()
            divides = previous is not None and (
                previous.kind in ("number", "string", "template", "regex")
                or (previous.kind == "word" and previous.text not in REGEX_AFTER_WORDS)
                or (previous.kind == "punct" and previous.text in ")]}"))
            if divides:
                tokens.append(Token("punct", "/", line)); i += 1
                continue
            j, in_class = i + 1, False
            while j < n and (source[j] != "/" or in_class):
                if source[j] == "\n":
                    raise NotTypeScript(line, "a regular expression is still open at the end of its line")
                if source[j] == "\\":
                    j += 1
                elif source[j] == "[":
                    in_class = True
                elif source[j] == "]":
                    in_class = False
                j += 1
            j += 1
            while j < n and (source[j].isalnum()):
                j += 1
            tokens.append(Token("regex", source[i:j], line)); i = j
        elif WORD.match(source, i):
            m = WORD.match(source, i)
            tokens.append(Token("word", m.group(0), line)); i = m.end()
        elif NUMBER.match(source, i):
            m = NUMBER.match(source, i)
            tokens.append(Token("number", m.group(0), line)); i = m.end()
        else:
            if ch in "({[":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            tokens.append(Token("punct", ch, line)); i += 1
    return tokens


def unbalanced(tokens: list[Token]) -> NotTypeScript | None:
    """A bracket that never closes, or closes the wrong thing."""
    pairs, stack = {")": "(", "]": "[", "}": "{"}, []
    for token in tokens:
        if token.kind != "punct":
            continue
        if token.text in "([{":
            stack.append(token)
        elif token.text in pairs:
            if not stack or stack[-1].text != pairs[token.text]:
                return NotTypeScript(token.line, f"`{token.text}` closes something that was never opened")
            stack.pop()
    if stack:
        return NotTypeScript(stack[-1].line, f"`{stack[-1].text}` is never closed")
    return None


# --- hidden characters --------------------------------------------------------

# Unicode "tag" characters mirror ASCII invisibly (U+E0041 is an invisible "A"),
# which is how a whole instruction is smuggled into a line that looks empty.
# Bidi overrides and isolates reorder what a reviewer sees without changing what
# a model reads (the "Trojan Source" attack). No test file needs either.
INVISIBLE = re.compile("[\U000E0000-\U000E007F\u202A-\u202E\u2066-\u2069]")

# Removed before matching, so `ig\u200bnore` still reads as "ignore". These do
# turn up innocently (a zero-width space pasted from a web page), so they are
# not a refusal on their own.
ZERO_WIDTH = re.compile("[\u200B-\u200D\u2060\uFEFF\u00AD]")

# Latin letters' look-alikes. NFKC folds the fancy forms (fullwidth, bold
# mathematical) but not other scripts, and "\u0456gnore" (a Cyrillic i) is the
# oldest trick there is.
CONFUSABLES = str.maketrans({
    "\u0430": "a", "\u0435": "e", "\u043e": "o", "\u0440": "p", "\u0441": "c", "\u0443": "y",
    "\u0445": "x", "\u0456": "i", "\u0458": "j", "\u0455": "s", "\u0501": "d", "\u04bb": "h",
    "\u0261": "g", "\u03bf": "o", "\u03b1": "a", "\u03b5": "e", "\u03b9": "i", "\u03bd": "v",
    "\u03c1": "p", "\u03ba": "k", "\u03c4": "t",
})


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", ZERO_WIDTH.sub("", text)).translate(CONFUSABLES).lower()


# --- text addressed to a model ------------------------------------------------

_MODEL = (r"(?:ai|a\.i\.|llm|large language model|language model|chat ?gpt|gpt-?\d[\w.]*|claude|gemini|"
          r"copilot|openai|anthropic|chatbot|ai assistant|ai model|ai agent)")

# (what it is, pattern). Each one was run over 570 real Selenium files from 288
# GitHub repositories plus every sample in this repo, and tuned until it found
# nothing there — see tests/test_screen.py for the corpus figures and for the
# innocent phrases each was narrowed to let through ("you are now logged in",
# "ignore eslint rules", "act as admin", "do not log the user out").
INJECTION: list[tuple[str, re.Pattern[str]]] = [
    ("an instruction to ignore earlier instructions", re.compile(
        r"\b(?:ignore|disregard|forget|override|overwrite|bypass|don'?t follow|do not follow|stop following)\b"
        r"(?:[^\w.!?;]+\w+){0,4}?[^\w.!?;]+(?:"
        # Words that only ever mean a model's instructions take any qualifier...
        r"(?:all|any|every|previous|prior|above|earlier|preceding|original|initial|your|system|these|those)\b"
        r"(?:[^\w.!?;]+\w+){0,3}?[^\w.!?;]+(?:instructions?|prompts?|playbook|guidelines|directives?|"
        r"system (?:message|prompt)|guardrails)\b"
        # ...while "rules" needs one that points backwards: "ignore all eslint rules" is a lint comment.
        r"|(?:previous|prior|above|earlier|preceding|original|initial|your|system|playbook)\b"
        r"(?:[^\w.!?;]+\w+){0,3}?[^\w.!?;]+rules\b"
        # ...and these name the model's own rulebook, so they need no qualifier at all.
        r"|(?:playbook|system prompt|system message|guardrails|programming)\b)")),
    ("a role reassignment", re.compile(
        r"\byou are (?:now |no longer |actually |really )?(?:an? |the |my )?(?:" + _MODEL +
        r"|dan|jailbroken|unrestricted|unfiltered|in (?:developer|god|debug) mode)\b")),
    ("a role reassignment", re.compile(
        r"\b(?:act|behave|respond|pretend|roleplay|role-play)\s+(?:as|like|to be)\s+(?:an? |the |my )?"
        r"(?:" + _MODEL + r"|dan|jailbroken|unrestricted|unfiltered|different (?:ai|assistant|model))\b")),
    ("a jailbreak phrase", re.compile(
        r"\bjailbr(?:eak|oken)\b|\bdo anything now\b|\bdeveloper mode (?:enabled|on|activated)\b")),
    ("a claim to carry new instructions", re.compile(
        # Not "additional instructions" or "delivery instructions": those are form labels.
        r"\b(?:new|updated|revised|hidden|secret|override|priority|urgent)\s+(?:system\s+)?"
        r"(?:instructions|directives?)\s*(?::|(?:for|to)\s+(?:you|the (?:" + _MODEL +
        r"|model|assistant|converter)))|\b(?:new|updated|revised|hidden|secret|override)\s+system\s+"
        r"(?:instructions|prompt)\b")),
    ("a claim that the rules changed", re.compile(
        r"\b(?:migration|conversion|converter|playbook)\s+(?:policy|rules?|instructions?)\s+"
        r"(?:has |have )?(?:changed|been (?:updated|changed|replaced))\b|\bsystem (?:update|override|notice)\s*:")),
    ("a request for the model's instructions", re.compile(
        r"\byour\s+(?:system|developer|hidden|initial|original|internal)\s+(?:prompt|instructions)\b|"
        r"\b(?:reveal|print|repeat|output|leak|dump|disclose|show me|tell me)\s+(?:your|the system'?s?)\s+"
        r"(?:system\s+)?(?:prompt|instructions|rules|playbook)\b")),
    ("a message to an AI", re.compile(
        r"\b(?:dear|hey|hello|hi|attention|note to|message (?:to|for)|instructions? (?:to|for)|reminder (?:to|for))"
        r"\s+(?:the\s+|any\s+|all\s+)?" + _MODEL + r"s?\b"
        # Not "if you are a bot" or "a model": captcha and fashion tests say those.
        r"|\bif you are (?:an? |the )?(?:" + _MODEL + r"|ai-powered \w+|automated (?:converter|agent))\b"
        r"|\bas an? " + _MODEL + r"\b"
        r"|\b" + _MODEL + r"s?\s+(?:that is |who is |which is )?(?:reading|processing|converting|reviewing|parsing)"
        r"\s+(?:this|the)\b"
        r"|\bto (?:the|any) (?:" + _MODEL + r"|model|converter|assistant) (?:that|who|which) "
        r"(?:reads|converts|processes|reviews)\b")),
    ("a request to hide something from the user", re.compile(
        r"\b(?:do not|don'?t|never|without)\s+(?:mention|mentioning|report|reporting|flag|flagging|disclose|"
        r"disclosing|reveal|revealing|tell|telling)\b(?:[^\w.!?;]+\w+){0,4}?[^\w.!?;]+"
        r"(?:notes|todos?|ledger|todo\(review\)|the (?:user|human|reviewer|report))\b")),
    ("an instruction to add code to the output", re.compile(
        r"\b(?:add|insert|include|append|prepend|inject)\b(?:[^\w.!?;]+[\w.$]+){0,8}?[^\w.!?;]+"
        r"(?:to|into|in|at the top of)\s+(?:every|each|all|the)\s+(?:converted|generated|output|resulting|"
        r"migrated|translated)\s+(?:files?|code|specs?|tests?|output)\b")),
    ("a download-and-run command", re.compile(
        r"\b(?:curl|wget)\b[^\n|;]{0,200}\|\s*(?:sudo\s+)?(?:ba|z|da)?sh\b|"
        r"\b(?:iex|invoke-expression)\b.{0,60}\bdownloadstring\b")),
]

# Structure a chat model treats as the start of another turn. Matched on the
# raw text (case-insensitive), because the brackets and bars are the signal.
MARKERS: list[tuple[str, re.Pattern[str]]] = [
    ("a chat-template marker", re.compile(
        r"<\|\s*(?:im_start|im_end|endoftext|system|assistant|user|eot_id|start_header_id)\s*\|>|"
        r"\[/?INST\]|<<\s*/?SYS\s*>>", re.IGNORECASE)),
    ("a conversation role tag", re.compile(
        r"</?\s*(?:system|assistant|human|instructions?|system[_-]?prompt)\s*>", re.IGNORECASE)),
    ("one of the converter's own prompt tags", re.compile(
        r"</?\s*(?:source_file|converted_file|conversion_result|validation_reports|previous_conversion|"
        r"critic_fixes)\b", re.IGNORECASE)),
    ("a conversation role label", re.compile(
        # A turn header followed by an instruction, not "System: macOS only".
        r"^[\s/*#>-]*(?:system|assistant)\s*:\s*(?:you\b|ignore\b|disregard\b|from now on\b|new instructions\b|"
        r"the (?:user|assistant|model)\b)", re.IGNORECASE | re.MULTILINE)),
]


@dataclass(frozen=True)
class Hit:
    line: int
    what: str
    excerpt: str


class _Lines:
    """Map an offset in a stitched-together text back to a source line."""

    def __init__(self):
        self.parts: list[str] = []
        self.starts: list[int] = []
        self.lines: list[int] = []
        self.size = 0

    def add(self, text: str, line: int, joiner: str = " ") -> None:
        piece = (joiner if self.parts else "") + text
        self.starts.append(self.size); self.lines.append(line)
        self.parts.append(piece); self.size += len(piece)

    def text(self) -> str:
        return "".join(self.parts)

    def line_at(self, offset: int) -> int:
        return self.lines[max(0, bisect_right(self.starts, offset) - 1)]


def _search(patterns, stitched: _Lines, prepare) -> Hit | None:
    text = prepare(stitched.text())
    for what, pattern in patterns:
        m = pattern.search(text)
        if m:
            excerpt = re.sub(r"\s+", " ", m.group(0)).strip()
            return Hit(stitched.line_at(m.start()), what, excerpt[:80])
    return None


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", normalize(text))


def addressed_to_model(source: str, tokens: list[Token] | None) -> Hit | None:
    """The first text in the file that reads as speaking to the model, if any.

    Read twice. Once as raw lines joined with spaces, which catches a phrase
    split across comment lines and anything a tokenizer might misfile. Once as
    the file's prose only — comments and strings — with `"ig" + "nore"` glued
    back together, which is how a model would read a string built in pieces.
    """
    raw = _Lines()
    for number, text in enumerate(source.splitlines(), start=1):
        raw.add(text, number, joiner="\n")
    hit = _search(MARKERS, raw, lambda t: ZERO_WIDTH.sub("", unicodedata.normalize("NFKC", t)))
    if hit:
        return hit
    hit = _search(INJECTION, raw, _flat)
    if hit or tokens is None:
        return hit
    prose = _Lines()
    for index, token in enumerate(tokens):
        if token.kind not in ("comment", "string", "template"):
            continue
        glued = (index >= 2 and tokens[index - 1].text == "+"
                 and tokens[index - 2].kind in ("string", "template"))
        prose.add(token.text, token.line, joiner="" if glued else " ")
    return _search(INJECTION, prose, _flat)


# --- is it code, and is it Selenium? ------------------------------------------

# Every TypeScript keyword, reserved or contextual. Two plain words side by side
# on one line are prose — `click the button` — unless one of them is on this
# list: `export default`, `private readonly`, `asserts value is Foo`.
KEYWORDS = frozenset("""
abstract accessor any as asserts async await bigint boolean break case catch class const constructor
continue debugger declare default delete do else enum export extends false finally for from function
get global goto if implements import in infer instanceof interface is keyof let module namespace never
new null number object of out override package private protected public readonly require return
satisfies set static string super switch symbol this throw true try type typeof undefined unique
unknown using var void while with yield
""".split())

SELENIUM_MODULE = re.compile(r"^selenium-webdriver(?:/[\w./-]+)?$")

# Calls on a driver or element the file did not import itself — `driver` handed
# in by a helper. The names a file DID import are read from its own import line.
SELENIUM_MEMBERS = frozenset({"findElement", "findElements", "sendKeys", "switchTo", "executeScript",
                              "executeAsyncScript", "manage", "navigate", "getAttribute", "getText",
                              "isDisplayed", "elementLocated", "elementIsVisible", "urlContains",
                              "titleIs", "forBrowser", "takeScreenshot", "getCurrentUrl", "quit",
                              "getWindowHandle", "getAllWindowHandles", "actions", "wait", "sleep",
                              "get", "click", "clear", "setChromeOptions", "build"})


def prose_line(tokens: list[Token]) -> Token | None:
    """The first code line holding two plain words side by side, if any."""
    previous: Token | None = None
    for token in tokens:
        if token.kind == "comment":
            continue
        if (token.kind in ("word", "number") and previous is not None and previous.line == token.line
                and previous.kind in ("word", "number")
                and previous.text not in KEYWORDS and token.text not in KEYWORDS):
            return previous
        previous = token
    return None


def selenium_imports(tokens: list[Token]) -> tuple[set[str], set[int]]:
    """The names bound by this file's selenium-webdriver imports, and their lines.

    Every shape: `import { By as B }`, `import * as webdriver`, `import webdriver`,
    `const { By } = require(...)`, `import wd = require(...)`. Empty names and no
    lines when nothing imports it in code — a commented-out import is a comment.
    """
    names: set[str] = set()
    lines: set[int] = set()
    for index, token in enumerate(tokens):
        if token.kind != "string" or not SELENIUM_MODULE.match(token.text):
            continue
        before = [t for t in tokens[max(0, index - 3):index] if t.kind != "comment"]
        by_from = bool(before) and before[-1].kind == "word" and before[-1].text in ("from", "import")
        by_call = len(before) >= 2 and before[-2].text in ("require", "import") and before[-1].text == "("
        if not (by_from or by_call):
            continue
        first = index
        while first > 0 and not (tokens[first].kind == "word"
                                 and tokens[first].text in ("import", "const", "let", "var")):
            first -= 1
        statement = tokens[first:index]
        lines.update(range(tokens[first].line, token.line + 1))
        for position, part in enumerate(statement):
            if part.kind != "word" or part.text in KEYWORDS:
                continue
            following = statement[position + 1] if position + 1 < len(statement) else None
            if following is not None and following.text in (":", "as") and following.text != "as":
                continue  # `{ By: B }` binds B, not By
            if following is not None and following.kind == "word" and following.text == "as":
                continue  # `By as B` binds B
            names.add(part.text)
    return names, lines


def selenium_complaint(tokens: list[Token], source: str, filename: str) -> str:
    """Why this is not a Selenium file, or "" if it is one."""
    verdict = classify(source, filename or "pasted.ts")
    if not verdict.supported:
        return f"This is not a file the converter takes: {verdict.reason}."
    names, import_lines = selenium_imports(tokens)
    if not import_lines:
        return ("This file does not import selenium-webdriver in TypeScript code — a mention in a "
                "comment or a string does not count. Paste a TypeScript file that imports it.")
    for index, token in enumerate(tokens):
        if token.kind != "word" or token.line in import_lines:
            continue
        member = index > 0 and tokens[index - 1].text == "."
        if (member and token.text in SELENIUM_MEMBERS) or (not member and token.text in names):
            return ""
    return ("This file imports selenium-webdriver but never uses it, so there is nothing to convert. "
            "Paste a page object or a test that drives the browser.")


# --- the three questions a caller asks ----------------------------------------

REFUSED = "Nothing was sent to the model."


def _hidden(text: str) -> str:
    m = INVISIBLE.search(text)
    if not m:
        return ""
    line = text.count("\n", 0, m.start()) + 1
    return (f"Line {line} contains an invisible Unicode control character (U+{ord(m.group(0)):04X}), "
            f"which can hide text from whoever reads the file. {REFUSED}")


def _injected(text: str, tokens: list[Token] | None, where: str) -> str:
    hit = addressed_to_model(text, tokens)
    if hit is None:
        return ""
    return (f"Line {hit.line} of {where} looks like {hit.what}, aimed at the AI model rather than "
            f"part of a test: “{hit.excerpt}”. {REFUSED} Remove that text and paste the file again.")


def _code(text: str, where: str) -> tuple[list[Token] | None, str]:
    try:
        tokens = tokenize(ZERO_WIDTH.sub("", text))
    except NotTypeScript as exc:
        return None, f"{where.capitalize()} is not valid TypeScript: {exc.what} (line {exc.line})."
    broken = unbalanced(tokens)
    if broken:
        return None, f"{where.capitalize()} is not valid TypeScript: {broken.what} (line {broken.line})."
    words = prose_line(tokens)
    if words:
        excerpt = next((ln for n, ln in enumerate(text.splitlines(), 1) if n == words.line), "").strip()
        return None, (f"Line {words.line} of {where} is not TypeScript: “{excerpt[:80]}”. "
                      "Paste only the code of a TypeScript Selenium file.")
    return tokens, ""


def screen_source(source: str, filename: str = "") -> str:
    """Refuse a pasted Selenium file before a model sees it; "" when it may go.

    Order is the order of what is most useful to be told: hidden characters and
    text aimed at the model first (they are the reason, whatever else is wrong),
    then whether it is TypeScript at all, then whether it is Selenium.
    """
    where = "the file"
    if PurePath(filename or "pasted.ts").suffix.lower() not in (".ts", ".tsx", ""):
        return "Only TypeScript files (.ts) can be converted."
    complaint = _hidden(source)
    if complaint:
        return complaint
    try:
        tokens = tokenize(source)
    except NotTypeScript:
        tokens = None
    complaint = _injected(source, tokens, where)
    if complaint:
        return complaint
    tokens, complaint = _code(source, where)
    if complaint:
        return complaint
    return selenium_complaint(tokens, ZERO_WIDTH.sub("", source), filename)


def screen_companion(text: str, name: str = "") -> str:
    """The already-converted file sent beside the source. It is Playwright, so
    only the questions that apply to any file: hidden text, injection, code."""
    where = f"the companion {name}".strip() if name else "the companion"
    complaint = _hidden(text)
    if complaint:
        return complaint
    try:
        tokens = tokenize(text)
    except NotTypeScript:
        tokens = None
    return _injected(text, tokens, where) or _code(text, where)[1]


def screen_instruction(text: str) -> str:
    """A refine instruction is English on purpose — "use getByTestId" — so only
    hidden characters and text aimed past the playbook are refused."""
    if not text.strip():
        return ""
    complaint = _hidden(text)
    if complaint:
        return complaint
    hit = addressed_to_model(text, None)
    if hit is None:
        return ""
    return (f"That instruction looks like {hit.what} (“{hit.excerpt}”). Instructions here can "
            f"change how the file is converted, not what the converter is. {REFUSED}")
