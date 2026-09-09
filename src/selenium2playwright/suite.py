"""Step 9.1 — read a whole suite before converting any of it. No LLM.

Single-file mode has one input and one question: convert this. A folder has a
shape, and the shape has to be read first, because two facts decide everything
Phase 9 does afterwards:

  * **What each file is.** A page object, a test, a plain helper with no
    automation in it at all, or something outside the MVP. Only the first two
    are worth a model call; a helper is copied across untouched; the rest are
    refused by name, with the reason, rather than silently dropped.
  * **What imports what.** `tests/login.spec.ts` imports `pages/LoginPage`, so
    the page object has to be converted first — and once it is, the converted
    page object is exactly the context the test's conversion needs (the graph
    already takes companions as `context_paths`).

Turning the second fact into an order is a topological sort, and its layers are
what this module calls *waves*: wave 1 is everything that depends on nothing
else in the suite, wave 2 is everything whose in-suite imports are all in wave
1, and so on. Files inside one wave cannot affect each other, which is the
property step 9.2 needs to fan them out in parallel with `Send`.

A suite is converted whole or not at all. There used to be a per-kind cap here
that converted a slice of a big folder and copied the rest across, and it was a
bad trade twice over: the slice it chose was the leaves of the wave plan — the
driver factories and wrapper libraries, on one real repo not a single test file
— and a half-converted folder is not a result anybody can use. What replaced it
is a decision made before any model runs: the meter is charged for the whole
suite up front (`conversions`), so a folder that does not fit the day's budget
is refused entire, and the way to convert it is to clone the repo and run it
with your own key. Nobody should be asked to type a provider key into somebody
else's website, so the hosted demo does not offer that and never will.

Everything here is a pure function of the bytes on disk and that environment:
same folder, same manifest, every time. That is deliberate — a plan you cannot reproduce is not a
plan you can put in a report.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from fnmatch import fnmatch
from dataclasses import dataclass, replace
from pathlib import Path

from selenium2playwright.classify import Classification, classify

# Suffixes worth reading. Everything else in the tree (JSON, markdown, fixtures,
# screenshots) is not source we convert, so it is not in the manifest at all.
SOURCE_SUFFIXES = (".ts", ".tsx", ".js", ".mjs", ".cjs")

# Not source, but not ignorable either: a suite keeps its fixtures in JSON and
# imports them by name (`import loginData from "tests/testdata/login.json"`).
# Nothing converts them — they travel across untouched — but they have to
# travel, or the converted tree imports a file that is not there and the
# compile gate fails a file whose only sin was reading its own test data.
DATA_SUFFIXES = (".json",)

# JSON that is configuration rather than fixture: never imported by test code,
# sometimes megabytes (`package-lock.json`), and not the suite's to carry.
CONFIG_JSON = frozenset({
    "package.json", "package-lock.json", "tsconfig.json", "tsconfig.base.json",
    "jsconfig.json", ".eslintrc.json", "playwright.config.json",
})

def is_input(name: str) -> bool:
    """Is this file worth reading at all — source to convert, or a fixture to carry?

    One predicate, because there are four doors into this project (the folder
    walk, a dropped zip, loose files, the sample reader) and a suite that came
    in through one of them missing its `login.json` would compile differently
    from the same suite that came in through another.
    """
    return (Path(name).suffix.lower() in SOURCE_SUFFIXES + DATA_SUFFIXES
            and Path(name).name not in CONFIG_JSON)


# Directories that are never input: dependencies, build output, our own output.
# Walking node_modules would take minutes and classify tens of thousands of
# files nobody asked about.
SKIP_DIRS = frozenset({
    "node_modules", ".git", ".s2p", "dist", "build", "out", "coverage",
    "playwright-report", "test-results", "__pycache__", ".venv",
})

# Covers every way TypeScript names another file:
#   import X from "spec"      import "spec"      export … from "spec"
#   require("spec")           import("spec")
IMPORT_PATTERN = re.compile(
    r"""(?:\bfrom\s*|\bimport\s*|\brequire\s*\(\s*|\bimport\s*\(\s*)['"]([^'"]+)['"]""")

# The extensions TypeScript will try when a specifier has none of its own.
# "../pages/LoginPage" on disk is LoginPage.ts; a folder import is its index.
RESOLUTION_ORDER = (".ts", ".tsx", ".d.ts", ".js", ".mjs", ".cjs", "/index.ts", "/index.js")

# One test case, in Mocha and in Jest alike: `it("…")` or `test("…")`, with any
# modifier chain in between — `it.only`, `it.skip`, `test.each`, which is why
# the call can open with a backtick as well as a bracket.
#
# `describe` is deliberately not counted: it groups cases, it is not one, and a
# file with three describes and thirty its should say thirty. The lookbehind
# keeps `suite.it(` and `foo_it(` out — a bare `it` is the only one that is the
# runner's.
#
# This is a count for a progress line, not an assertion: an `it(` inside a
# comment is counted, and no regex short of parsing the file would know better.
# The number is honest about scale, which is all it is asked to be.
CASE_PATTERN = re.compile(r"""(?<![\w$.])(?:it|test)(?:\.\w+)*\s*[(`]""")


def count_cases(source: str) -> int:
    """How many test cases are in this file. Heuristic, like everything here."""
    return len(CASE_PATTERN.findall(source))


# What we do with each kind of file, and why. The reason is written for the
# person reading the report, not for the code.
CONVERT, COPY, SKIP = "convert", "copy", "skip"

# Named and versioned like the conversion report, so a consumer can check the
# shape it is reading instead of guessing from the keys; a breaking change gets
# /v2, never a silent edit.
MANIFEST_SCHEMA = "s2p.suite-manifest/v1"


@dataclass(frozen=True)
class SuiteFile:
    """One file in the suite, and the plan for it."""

    path: str  # posix, relative to the suite root — the id used everywhere else
    kind: str  # page-object | test | support | unsupported
    action: str  # convert | copy | skip
    reason: str  # human-readable; printed verbatim in the report
    classification: Classification
    imports: tuple[str, ...] = ()  # in-suite files this one imports, by path
    imported_by: tuple[str, ...] = ()  # the reverse edge; who needs this converted first
    external_imports: tuple[str, ...] = ()  # packages: selenium-webdriver, chai, node:os …
    data_imports: tuple[str, ...] = ()  # JSON fixtures it reads; copied across, never converted
    lines: int = 0
    cases: int = 0  # test cases inside it; 0 for a page object, which has none
    wave: int = 0  # 1-based; 0 means "not converted, so not in any wave"


@dataclass(frozen=True)
class Manifest:
    """The whole plan for one folder: every file, and the order to convert in."""

    root: str
    files: tuple[SuiteFile, ...]
    waves: tuple[tuple[str, ...], ...]  # wave 1 first; each is a set of paths
    notes: tuple[str, ...] = ()  # anything the reader should know
    assets: tuple[str, ...] = ()  # JSON fixtures some file imports; copied across as-is

    @property
    def convertible(self) -> tuple[SuiteFile, ...]:
        return tuple(f for f in self.files if f.action == CONVERT)

    def counts(self) -> dict[str, int]:
        return {action: sum(1 for f in self.files if f.action == action)
                for action in (CONVERT, COPY, SKIP)}


def discover(root: Path) -> list[Path]:
    """Every source file and fixture under `root`, sorted, minus the skipped dirs.

    The skipped directories are pruned from the walk rather than filtered out of
    its results: `node_modules` in a real suite holds tens of thousands of files,
    and the fastest way to not classify them is to never look inside.
    """
    found = []
    for folder, subfolders, filenames in os.walk(root):
        subfolders[:] = sorted(d for d in subfolders if d not in SKIP_DIRS)
        for name in sorted(filenames):
            if is_input(name):
                found.append(Path(folder) / name)
    return sorted(found)


def import_specifiers(source: str) -> list[str]:
    """The raw strings this file imports, in file order, without duplicates.

    Regex, not a TypeScript parser, for the same reason classify.py uses one:
    import statements are the most regular lines in the language, and the cost
    of being wrong here is a missing edge in the wave plan — which shows up as
    a file converted without its companion, not as bad output.
    """
    return list(dict.fromkeys(IMPORT_PATTERN.findall(source)))


def resolve_import(specifier: str, source_path: str, known: set[str]) -> str | None:
    """A specifier turned into a path in this suite, or None.

    None means "not a file in this folder" — either a package (`chai`,
    `selenium-webdriver`, `node:os`) or a path that points outside the suite.
    Both are useful to know and neither is an error.

    Two spellings reach a file here. The relative one (`../pages/LoginPage`) is
    the obvious one. The other is a **path alias** — `@lib/test.metadata`,
    `@/pages/index`, `~/config/env` — which a real repository defines in its own
    tsconfig and uses everywhere in place of relative paths.

    Reading only the relative spelling made an alias-based suite look like a
    folder of files that import nothing from each other, and everything
    downstream believed it: the wave plan put mutually dependent files in one
    wave, the per-file gates compiled each file with no companions beside it, so
    every aliased import failed TS2307 and the reflection loop spent all three
    attempts on imports that were already correct. One live suite scored 0 of 3
    that way with every file marked needs-review.

    An alias is followed only when the tree contains what it points at, which is
    what keeps a real npm scope out: `@lib/x` resolves because `lib/x.ts` is
    right there, `@playwright/test` does not because nothing here answers to it.

    The third spelling has no prefix at all: `tests/pages/login.page`, which is
    what `"baseUrl": "."` means — resolve a bare specifier against the project
    root before falling back to node_modules. It is exactly how TypeScript
    itself reads one, and it is common enough that missing it cost a real repo
    eight unresolved imports and a test file that scored 3 of 4 gates. The same
    "only if the tree answers to it" rule keeps `chai` and `selenium-webdriver`
    out: they resolve to nothing here, so they stay packages.
    """
    if specifier.startswith("."):
        base = _normalise((Path(source_path).parent / specifier).as_posix())
    elif specifier.startswith(("@", "~")):
        head, _, rest = specifier.partition("/")
        if not rest:
            return None
        base = rest if head in ("@", "~") else f"{head.lstrip('@~')}/{rest}"
    elif specifier.startswith("node:"):
        return None  # a builtin can never be a file here, whatever the tree holds
    else:
        base = _normalise(specifier)
    if base in known:
        return base
    for ending in RESOLUTION_ORDER:
        candidate = base + ending
        if candidate in known:
            return candidate
    return None


def _normalise(path: str) -> str:
    """Collapse `a/../b` and `./b` by hand — the file need not exist to do it."""
    parts: list[str] = []
    for part in path.split("/"):
        if part in ("", "."):
            continue
        if part == ".." and parts and parts[-1] != "..":
            parts.pop()
        else:
            parts.append(part)
    return "/".join(parts)


def decide(classification: Classification) -> tuple[str, str, str]:
    """(kind, action, reason) for one classified file.

    classify() answers a single-file question — "can I convert this one?" — and
    says no to a plain helper because it contains no Selenium. In a folder that
    same answer means something different and much less alarming: nothing to
    convert, so copy it across. Splitting the two cases here is the whole reason
    this function exists.
    """
    if classification.supported:
        if classification.runner == "none":
            return "page-object", CONVERT, "page object / helper driving Selenium"
        return "test", CONVERT, f"{classification.runner} test file"
    if classification.automation == "unknown":
        return ("support", COPY,
                "no automation library in it; carried over to the converted suite unchanged")
    return "unsupported", SKIP, classification.reason


def plan_waves(files: dict[str, SuiteFile]) -> tuple[tuple[tuple[str, ...], ...], list[str]]:
    """Layer the convertible files so nothing is converted before what it imports.

    Kahn's algorithm, one layer at a time: take everything with no outstanding
    in-suite dependency, that is wave 1; drop those from everyone else's
    dependency list; repeat. Only convertible files take part — a copied helper
    needs no conversion, so nothing waits on it.

    A cycle (two files importing each other) has no valid order at all. Rather
    than loop forever or drop the files, the rest go into one final wave with a
    note saying so: they will convert, just without the guarantee that each one
    sees its companion already converted.
    """
    pending = {p: {d for d in f.imports if files[d].action == CONVERT}
               for p, f in files.items() if f.action == CONVERT}
    waves: list[tuple[str, ...]] = []
    notes: list[str] = []
    while pending:
        ready = tuple(sorted(p for p, deps in pending.items() if not deps))
        if not ready:  # every survivor is in or behind an import cycle
            stuck = tuple(sorted(pending))
            notes.append("import cycle between " + ", ".join(stuck) +
                         " — converted together in the last wave, none first")
            waves.append(stuck)
            break
        waves.append(ready)
        done = set(ready)
        pending = {p: deps - done for p, deps in pending.items() if p not in done}
    return tuple(waves), notes


def scan(root: Path) -> Manifest:
    """Read a folder and return the whole plan for it. Reads files; calls no model."""
    root = Path(root)
    if not root.is_dir():
        raise NotADirectoryError(f"{root} is not a directory")
    paths = discover(root)
    sources = {p.relative_to(root).as_posix(): p.read_text(encoding="utf-8", errors="replace")
               for p in paths}
    return scan_sources(sources, root.as_posix())


def scan_sources(sources: dict[str, str], root: str = "uploaded files") -> Manifest:
    """`scan`, for a tree that is already text: the same plan, with no disk.

    The folder walk is the only part of `scan` that needs a filesystem; the
    classification, the import graph and the waves work on strings. Splitting
    it here lets anything holding an uploaded tree — the page, the guard —
    plan it without first writing it somewhere, which is what lets the guard
    charge for the files that will be converted rather than for every file sent.

    `discover`'s two rules are applied to the keys so a text tree and the same
    tree on disk give the same answer: files in a `SKIP_DIRS` directory are not
    part of the plan, and neither is anything `is_input` would not have read.

    JSON fixtures come in through the same door and are then held apart, because
    they are input without being *plan*: nothing classifies them, nothing
    converts them, and nothing waits on them — but a spec that reads one does
    not compile without it, so they have to be resolvable and they have to
    travel.
    """
    here = {path: text for path, text in sources.items()
            if not (set(Path(path).parts[:-1]) & SKIP_DIRS)}
    sources = {path: text for path, text in here.items()
               if Path(path).suffix.lower() in SOURCE_SUFFIXES}
    # Fixtures are held apart from `sources` on purpose. They are not classified,
    # not converted and not in any wave — putting them in the import graph would
    # ask the planner to order a file it has no opinion about — but they must
    # still be resolvable, or `tests/testdata/login.json` reads as an npm package.
    assets = {path for path in here
              if Path(path).suffix.lower() in DATA_SUFFIXES
              and Path(path).name not in CONFIG_JSON}
    known = set(sources)

    files: dict[str, SuiteFile] = {}
    for path, source in sources.items():
        classification = classify(source, path)
        kind, action, reason = decide(classification)
        specifiers = import_specifiers(source)
        inside = [r for r in (resolve_import(s, path, known) for s in specifiers) if r]
        data = [r for r in (resolve_import(s, path, assets) for s in specifiers) if r]
        files[path] = SuiteFile(
            path=path, kind=kind, action=action, reason=reason, classification=classification,
            imports=tuple(dict.fromkeys(inside)),
            data_imports=tuple(dict.fromkeys(data)),
            external_imports=tuple(s for s in specifiers
                                   if not s.startswith(".")
                                   and resolve_import(s, path, known | assets) is None),
            lines=source.count("\n") + (0 if source.endswith("\n") or not source else 1),
            cases=count_cases(source) if kind == "test" else 0,
        )

    # The reverse edge, filled in once every file is known: who is waiting on this one.
    for path in list(files):
        for target in files[path].imports:
            files[target] = _with_dependent(files[target], path)

    waves, notes = plan_waves(files)
    for number, wave in enumerate(waves, 1):
        for path in wave:
            files[path] = replace(files[path], wave=number)
    if not files:
        notes.append(f"no TypeScript or JavaScript source files under {root}")
    return Manifest(root=root, files=tuple(files[p] for p in sorted(files)),
                    waves=waves, notes=tuple(notes),
                    assets=tuple(sorted({a for f in files.values() for a in f.data_imports})))


def selected(path: str, patterns: list[str]) -> bool:
    """Does --only cover this file? No patterns means everything.

    A pattern matches the relative path (`pages/*.ts`) or the bare name
    (`LoginPage.ts`), because both are what a person types.
    """
    return not patterns or any(fnmatch(path, p) or fnmatch(Path(path).name, p) for p in patterns)


def conversions(tree: dict[str, str], only: list[str] | None = None) -> int:
    """How many files a run over this tree will send to the model.

    This is the number the meter should charge and the page should quote, and
    it is the same number in both places because both call this. It is smaller
    than `len(tree)` twice over: support files are copied, not converted, and
    `--only` narrows what is left. A suite that is two page objects, a test and
    a dozen helpers costs three, and a visitor who asks for one file pays for one.
    """
    patterns = [p for p in (only or []) if isinstance(p, str) and p.strip()]
    manifest = scan_sources(tree)
    return sum(1 for f in manifest.convertible if selected(f.path, patterns))


def census(files: Iterable[SuiteFile]) -> dict[str, int]:
    """How many page objects, test files and test cases are in this group.

    The vocabulary a progress line needs. "6 files in 2 waves" says nothing a
    person watching can picture; "6 page objects, then 6 Mocha test files with
    41 tests between them" is the same run described in the words they used
    when they wrote it.

    Takes any iterable of files, not a manifest, because the two callers want
    different groups out of the same scan: the whole suite for the "found"
    line, and one wave's worth for the line that names what is converting now.
    """
    files = list(files)
    return {
        "page_objects": sum(1 for f in files if f.kind == "page-object"),
        "tests": sum(1 for f in files if f.kind == "test"),
        "cases": sum(f.cases for f in files),
        "support": sum(1 for f in files if f.kind == "support"),
    }


def _with_dependent(item: SuiteFile, dependent: str) -> SuiteFile:
    return replace(item, imported_by=tuple(sorted(set(item.imported_by) | {dependent})))


def manifest_json(manifest: Manifest) -> dict:
    """The manifest as plain data, ready for json.dumps — the artifact of 9.1."""
    return {
        "schema": MANIFEST_SCHEMA,
        "root": manifest.root,
        "counts": manifest.counts(),
        "waves": [list(wave) for wave in manifest.waves],
        "notes": list(manifest.notes),
        "assets": list(manifest.assets),
        "files": [{
            "path": f.path, "kind": f.kind, "action": f.action, "reason": f.reason,
            "wave": f.wave, "lines": f.lines,
            "language": f.classification.language,
            "automation": f.classification.automation,
            "runner": f.classification.runner,
            "imports": list(f.imports), "imported_by": list(f.imported_by),
            "external_imports": list(f.external_imports),
            "data_imports": list(f.data_imports),
        } for f in manifest.files],
    }


# --- a suite as text, for callers with no filesystem here ----------------------
#
# Everything above this line takes a directory, because that is what `s2p suite`
# has and it is the honest shape for a folder. A browser has neither: what it
# has is a handful of uploaded files, or a zip, and the bytes inside them.
#
# `source_tree` is that second shape — relative path to text, the same keys the
# manifest and the ledger already use — and `materialize` turns it back into the
# first one inside a temporary directory the caller never names. That is the
# whole trick, and it is what lets the suite graph stay exactly as step 9.2 and
# 9.3 wrote it: it still walks a folder, still copies support files across, still
# compiles a real tree with a real `tsc`. It just does it somewhere disposable.
#
# The safety property lives in `safe_path`, and it has to be absolute: these keys
# come from a stranger's zip on a public host, and a key of `../../etc/cron.d/x`
# would be a file write outside the workspace. So the rule is a whitelist of
# shapes rather than a blacklist of tricks.

# A demo tree is capped on both axes because they fail differently: too many
# files is a bill, too many bytes is memory. Both are generous for a real page
# object suite and hopeless for anyone trying to use this as free compute.
MAX_TREE_FILES = int(os.environ.get("S2P_MAX_TREE_FILES") or 40)
MAX_TREE_BYTES = int(os.environ.get("S2P_MAX_TREE_BYTES") or 2 * 1024 * 1024)

# One path segment: no separators (so it cannot descend on its own), no leading
# dot (so no dotfiles and, more to the point, no ".."), and nothing exotic.
_SEGMENT = re.compile(r"^(?!\.)[A-Za-z0-9._-]{1,128}$")


def safe_path(name: str) -> str:
    """A relative posix path this is willing to create, or "" if it is not.

    Returns rather than raises because every caller wants to say *which* key was
    bad in a sentence, and none of them want a traceback. The checks are on the
    shape:

    * not absolute, and no drive letter or UNC prefix — `/etc/passwd` and
      `C:\\Windows\\x` are both out;
    * no backslashes at all, because a Windows-shaped key would arrive as one
      segment here and become a path later;
    * every segment matches `_SEGMENT`, which excludes `..` by excluding a
      leading dot;
    * at most 8 levels deep, because nothing real needs more and a deep tree is
      a cheap way to make a filesystem unhappy.
    """
    text = (name or "").strip().replace("\\", "/")
    if not text or text.startswith("/") or ":" in text:
        return ""
    parts = [p for p in text.split("/") if p]
    if not parts or len(parts) > 8:
        return ""
    if not all(_SEGMENT.match(part) for part in parts):
        return ""
    return "/".join(parts)


def check_tree(tree: dict[str, str]) -> str:
    """Say what is wrong with an uploaded tree, or "" if nothing is.

    Called in three places on purpose — the page before it sends, the guard
    before it authorizes, and the graph before it writes — because each of them
    is the last line of defence for a different caller.
    """
    if not tree:
        return "No files. Upload the TypeScript files, or a zip of the folder."
    if len(tree) > MAX_TREE_FILES:
        return f"{len(tree)} files. The limit is {MAX_TREE_FILES} per suite."
    total = 0
    for name, text in tree.items():
        if not safe_path(name):
            return (f"`{name}` is not a name this will create. Use a relative path "
                    "like `pages/LoginPage.ts` — no leading slash, no `..`.")
        total += len((text or "").encode("utf-8"))
    if total > MAX_TREE_BYTES:
        return f"That is {total // 1024} KB. The limit is {MAX_TREE_BYTES // 1024} KB per suite."
    if not any(Path(name).suffix.lower() in SOURCE_SUFFIXES for name in tree):
        return ("None of those files are source files this can convert "
                f"({', '.join(SOURCE_SUFFIXES)}).")
    return ""


def materialize(tree: dict[str, str], root: Path) -> Path:
    """Write a text tree into a real directory, and hand back the directory.

    Refuses the whole tree rather than skipping a bad key: a suite that silently
    dropped one file would convert and compile and be wrong in a way nobody
    would look for.
    """
    complaint = check_tree(tree)
    if complaint:
        raise ValueError(complaint)
    root.mkdir(parents=True, exist_ok=True)
    for name, text in tree.items():
        target = root / safe_path(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text or "", encoding="utf-8")
    return root
