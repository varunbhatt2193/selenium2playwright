"""Step 9.3 — assemble: turn twelve per-file results into one deliverable.

Step 9.2 converted a folder and printed a row per file. Every one of those rows
is a *local* claim: this file compiled, on its own, against the companions it
imported. Nobody has yet asked the question the person receiving the folder
actually has —

    does this tree work as one project, and what did I lose?

That question has four parts, and this module answers all four. None of them
need a model; they are all evidence.

  1. **The whole tree, compiled as one project.** `tsc --noEmit` over every
     file in the output at once — converted files, copied support files, all of
     them. A per-file gate cannot catch a page object whose method two specs
     call with different argument counts, because it never saw both specs. This
     can, and it is the single fact that decides whether the folder is usable.

  2. **The scorecard.** The per-file rows added up: how many passed, which
     gates held across the suite, how many laps it took, what it cost.

  3. **The parity ledger.** For every converted file, the public API of the
     source next to the public API of the result: what was **kept**, what looks
     **renamed**, and what was **removed** — with the model's own reason where
     it gave one. The parity *gate* (step 4.4) compares tests and assertions
     inside one file; this compares the surface other files call, which is the
     half no gate was watching. Renames are a guess by name similarity and are
     labelled as such; a removal with no explanation is the report's loudest
     line, because that is the shape of a silent loss.

  4. **The consolidated TODO(review) ledger** — playbook rule 25. Every TODO in
     the tree in one list, de-duplicated: a converted page object is context for
     the spec that imports it, so the same TODO comes back reported twice, once
     with the other file's path glued to the front (observed live in 9.2). One
     line per distinct task, with every place it appears.

The report is markdown because the audience is a person, and JSON because the
audience is also a build. Both are the same facts.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

from selenium2playwright.env import SANDBOX
from selenium2playwright.schemas import ValidationReport
from selenium2playwright.validators.compile import MISSING_MODULE, missing_dependency
from selenium2playwright.suite import CAP_INVITATION

MEMBERS = SANDBOX / "members.cjs"
PARITY = SANDBOX / "parity.cjs"

# Versioned like every other artifact. The 9.2 run document (s2p.suite-run/v1)
# is still exactly what it was; this one is that plus the four assembled facts.
REPORT_SCHEMA = "s2p.suite-report/v1"

REPORT_NAME = "conversion-report.md"

# Two names are called a rename only when they are this similar, and only when
# both sides are otherwise unexplained leftovers. The cutoff is deliberately
# high: calling a removal a rename hides a loss, while calling a rename a
# removal merely makes the report noisier, and the added-members list beside it
# gives the reader what they need to judge.
RENAME_RATIO = 0.7

TODO_MARK = "TODO(review)"
# `pages/LoginPage.ts: TODO(review): confirm the locator` — the path prefix a
# file picks up when it was handed a companion that carried the TODO.
PATH_PREFIX = re.compile(r"^[\w./\\-]+\.(?:ts|tsx|js|mjs|cjs):\s*")
COMMENT_MARK = re.compile(r"^(?://+|/\*+|\*+|<!--)\s*|\s*(?:\*/|-->)$")
# A comment line that continues the one above it rather than starting something new.
CONTINUATION = re.compile(r"^(?://|\*|/\*)")


@dataclass(frozen=True)
class ApiChange:
    """One thing the source exposed, and what became of it."""

    kind: str  # member | test
    name: str  # as the source called it: "LoginPage.login", "Login > logs in"
    verdict: str  # kept | renamed | removed
    counterpart: str = ""  # what it is called now, when renamed
    reason: str = ""  # the model's own words, when it explained itself


@dataclass(frozen=True)
class FileLedger:
    """One converted file's public surface, before and after."""

    path: str
    changes: tuple[ApiChange, ...] = ()
    added: tuple[str, ...] = ()  # new names with no source counterpart
    note: str = ""  # why this file has no comparable surface

    def count(self, verdict: str) -> int:
        return sum(1 for change in self.changes if change.verdict == verdict)

    @property
    def losses(self) -> tuple[ApiChange, ...]:
        """Everything a reader has to look at: renames are a guess, removals are a loss."""
        return tuple(c for c in self.changes if c.verdict != "kept")

    @property
    def unexplained(self) -> int:
        return sum(1 for c in self.changes if c.verdict == "removed" and not c.reason)


@dataclass(frozen=True)
class TodoEntry:
    """One distinct task, and every place it was written."""

    text: str
    places: tuple[str, ...]  # "pages/LoginPage.ts:14", or just the path when reported only

    @property
    def files(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(place.split(":")[0] for place in self.places))


@dataclass(frozen=True)
class Assembly:
    """Everything 9.3 adds on top of the per-file outcomes."""

    tree: ValidationReport | None = None  # the whole-tree compile
    tree_error: str = ""  # why there is no tree report, when there is none
    # Whole-tree findings by whose fault they can be; see `split_tree_findings`.
    split: dict = field(default_factory=dict)
    files: int = 0  # how many .ts files were compiled together
    ledgers: tuple[FileLedger, ...] = ()
    todos: tuple[TodoEntry, ...] = ()
    scorecard: dict = field(default_factory=dict)
    notes: tuple[str, ...] = ()  # limitations worth printing, not errors
    markdown: str = ""
    report_path: str = ""

    @property
    def compiles(self) -> bool:
        """One bool for the headline. Unknown is not the same as fine — it is False."""
        return self.tree is not None and self.tree.passed

    @property
    def tree_findings(self) -> list:
        """Every error `tsc` printed, including the ones the gate excused.

        The gate holds absent-package errors apart so the critic is not sent
        after work it cannot do; a report shown to a person hides nothing, and
        `split` says which bucket each one landed in.
        """
        return [*self.tree.findings, *self.tree.excused] if self.tree else []

    @property
    def unexplained(self) -> int:
        return sum(ledger.unexplained for ledger in self.ledgers)


# --- reading the tree --------------------------------------------------------


def read_tree(root: Path) -> dict[str, str]:
    """Every TypeScript file under a folder, keyed by its relative path.

    Relative paths are the keys everywhere in this project — the compile gate
    needs them to resolve `../pages/LoginPage`, and the ledger needs them to put
    a source file next to its converted counterpart.
    """
    files = {}
    for path in sorted(root.rglob("*.ts")):
        parts = path.relative_to(root).parts
        if "node_modules" in parts or any(part.startswith(".") for part in parts):
            continue
        files[path.relative_to(root).as_posix()] = path.read_text(encoding="utf-8")
    return files


def compile_tree(files: dict[str, str]) -> tuple[ValidationReport | None, str]:
    """`tsc --noEmit` over the whole output at once. Returns (report, error).

    Imported here rather than at module scope so that reading this module does
    not require the sandbox to exist. A missing sandbox is reported as text, not
    raised: the conversion already happened, and losing the whole run because
    the compiler is not installed would be the wrong trade.
    """
    if not files:
        return None, "the output tree has no TypeScript files to compile"
    from selenium2playwright.validators.compile import compile_check

    try:
        return compile_check(files), ""
    except Exception as exc:  # missing sandbox, tsc timeout, unreadable output
        return None, f"{type(exc).__name__}: {exc}"


def node_inventory(script: Path, payload: object) -> object:
    """Run one of the sandbox's parse-only inventory scripts over JSON on stdin."""
    if not (SANDBOX / "node_modules/typescript/lib/typescript.js").exists():
        raise RuntimeError("TypeScript missing — run `npm install` inside sandbox/ first")
    proc = subprocess.run(["node", str(script)], input=json.dumps(payload),
                          cwd=SANDBOX, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(f"{script.name} failed (exit {proc.returncode}):\n{proc.stderr}")
    return json.loads(proc.stdout)


# --- the parity ledger -------------------------------------------------------


def api_names(inventory: dict) -> list[tuple[str, str]]:
    """One inventory flattened to [(kind, name)] in source order.

    A member is named `Class.member` so that two classes with a `open()` each
    stay apart, and a top-level export keeps its bare name.
    """
    names = []
    for entry in inventory.get("classes", []):
        for member in entry["members"]:
            names.append(("member", f"{entry['name']}.{member['name']}"))
    for item in inventory.get("top", []):
        names.append(("member", item["name"]))
    return names


def test_names(inventory: dict) -> list[tuple[str, str]]:
    """Every test in one file, `suite > name`, in source order."""
    return [("test", " > ".join(test["name"])) for test in inventory.get("tests", [])]


def pair_class_renames(before: dict, after: dict) -> dict[str, str]:
    """Map source class names to converted ones, so a class rename is not a bloodbath.

    If `LoginPage` came back as `LoginPageObject`, comparing members by
    `Class.member` would call every member removed and every member added. Class
    names are matched first — exactly where they can be, then by similarity —
    and members are compared inside the pair.
    """
    source = [c["name"] for c in before.get("classes", [])]
    converted = [c["name"] for c in after.get("classes", [])]
    mapping = {name: name for name in source if name in converted}
    left = [n for n in source if n not in mapping]
    right = [n for n in converted if n not in set(mapping.values())]
    for old, new in best_pairs(left, right):
        mapping[old] = new
    return mapping


def similarity(one: str, other: str) -> float:
    return SequenceMatcher(None, one.lower(), other.lower()).ratio()


def stem(name: str) -> str:
    """A member name with the packaging taken off, for comparing across idioms.

    The commonest real rename in this conversion is not a rewording, it is an
    idiom change: Selenium's `getFlashText()` returns a string, Playwright's
    `flashMessage` is a locator you assert on. Both are the same member of the
    page object under two conventions, so the accessor prefix and the "…Text"
    suffix are noise and everything that survives them is the actual name.
    """
    text = re.sub(r"[^a-z0-9]", "", name.lower())
    for prefix in ("waitfor", "verify", "assert", "fetch", "check", "get", "set", "read", "is", "has"):
        if text.startswith(prefix) and len(text) > len(prefix) + 2:
            text = text[len(prefix):]
            break
    for suffix in ("locator", "element", "string", "value", "text"):
        if text.endswith(suffix) and len(text) > len(suffix) + 2:
            text = text[: -len(suffix)]
            break
    return text


def affinity(one: str, other: str) -> float:
    """How likely two names are the same thing under two names. 0 to 1.

    Whole containment of one stem in the other is treated as certainty, because
    that is what an idiom rename looks like (`getFlashText` → `flashMessage`);
    anything else falls back to plain character similarity of the stems. What
    this deliberately will *not* do is pair two unrelated survivors just because
    they are the only ones left: `open` and `goto` are the same idea and score
    0.25, so the report calls one removed and the other added, and the reader
    decides. A missed rename is noise; an invented one is a hidden loss.
    """
    left, right = stem(one), stem(other)
    if not left or not right:
        return 0.0
    short, long = sorted((left, right), key=len)
    if len(short) >= 3 and short in long:
        return 1.0
    return similarity(left, right)


def qualified_affinity(one: str, other: str) -> float:
    """Members only pair inside the same class; `LoginPage.` is not evidence."""
    head, _, tail = one.rpartition(".")
    other_head, _, other_tail = other.rpartition(".")
    if head != other_head:
        return 0.0
    return affinity(tail, other_tail)


def best_pairs(left: list[str], right: list[str], score=affinity) -> list[tuple[str, str]]:
    """Greedy one-to-one matching of leftover names above RENAME_RATIO.

    Greedy on the best score rather than in list order, so a strong pairing is
    never stolen by a weaker one that happened to come first in the file.
    """
    candidates = sorted(((score(a, b), a, b) for a in left for b in right),
                        key=lambda item: (-item[0], item[1], item[2]))
    pairs, used_left, used_right = [], set(), set()
    for value, a, b in candidates:
        if value < RENAME_RATIO or a in used_left or b in used_right:
            continue
        used_left.add(a)
        used_right.add(b)
        pairs.append((a, b))
    return pairs


def compare(before: list[tuple[str, str]], after: list[tuple[str, str]],
            reason_for, score=affinity) -> tuple[list[ApiChange], list[str]]:
    """Source names against converted names: kept, renamed, removed, plus what is new.

    Duplicates are matched one for one (two tests with the same title are two
    tests). What is left over on each side is offered to the rename matcher; what
    survives that is a removal, and a removal is asked for a reason.
    """
    remaining = list(after)
    changes: list[ApiChange] = []
    missing: list[tuple[str, str]] = []
    for kind, name in before:
        if (kind, name) in remaining:
            remaining.remove((kind, name))
            changes.append(ApiChange(kind=kind, name=name, verdict="kept"))
        else:
            missing.append((kind, name))
    renames = dict(best_pairs([name for _, name in missing], [name for _, name in remaining], score))
    for kind, name in missing:
        new = renames.get(name, "")
        changes.append(ApiChange(kind=kind, name=name, verdict="renamed" if new else "removed",
                                 counterpart=new, reason=reason_for(name)))
    taken = set(renames.values())
    return changes, [name for _, name in remaining if name not in taken]


def reasons_from(outcome) -> object:
    """A lookup that answers "did the model say anything about this name?".

    The model's `notes` and `todos` are prose it wrote about its own decisions.
    If a removed member is mentioned there by name, that sentence is the reason
    the removal is not a silent loss — quoted, never paraphrased.
    """
    lines = [*getattr(outcome, "notes", ()), *getattr(outcome, "todos", ())]

    def reason_for(name: str) -> str:
        bare = name.split(".")[-1]
        if not bare:
            return ""
        pattern = re.compile(rf"\b{re.escape(bare)}\b")
        for line in lines:
            if pattern.search(line):
                return clean(line)
        return ""

    return reason_for


def ledgers(root: Path, out_root: Path, outcomes: list) -> tuple[list[FileLedger], list[str]]:
    """One ledger per converted file. Returns (ledgers, notes)."""
    sources, converted, notes = {}, {}, []
    for outcome in outcomes:
        source = root / outcome.path
        target = Path(outcome.written) if outcome.written else out_root / outcome.path
        if source.exists() and target.exists():
            sources[outcome.path] = source.read_text(encoding="utf-8")
            converted[outcome.path] = target.read_text(encoding="utf-8")
    try:
        members = node_inventory(MEMBERS, [sources, converted])
        tests = node_inventory(PARITY, [sources, converted])
    except Exception as exc:
        return ([FileLedger(path=o.path, note="the inventory could not be read") for o in outcomes],
                [f"the parity ledger is missing: {type(exc).__name__}: {exc}"])

    built = []
    for outcome in outcomes:
        path = outcome.path
        if path not in sources:
            # Nothing was written, so everything the source exposed is gone —
            # and the reason is the reason the file failed.
            built.append(FileLedger(path=path, note=outcome.reason or "no converted file was written"))
            continue
        before, after = members[0][path], members[1][path]
        classes = pair_class_renames(before, after)
        renamed_class = {old: new for old, new in classes.items() if old != new}
        source_api = [(kind, rename_class(name, renamed_class)) for kind, name in api_names(before)]
        reason_for = reasons_from(outcome)
        # Members and tests are matched in separate pools, so a lost test can
        # never be explained away as a renamed method.
        changes, added = compare(source_api, api_names(after), reason_for, qualified_affinity)
        test_changes, test_added = compare(test_names(tests[0][path]), test_names(tests[1][path]),
                                           reason_for, similarity)
        changes, added = changes + test_changes, added + test_added
        for old, new in renamed_class.items():
            notes.append(f"{path}: class {old} is now {new}; its members are compared under the new name")
        for side, inventory in (("source", before), ("converted", after)):
            for issue in inventory["issues"]:
                notes.append(f"{path}: {side} line {issue['line']}: {issue['message']}")
        built.append(FileLedger(path=path, changes=tuple(changes), added=tuple(added)))
    return built, notes


def rename_class(name: str, renamed: dict[str, str]) -> str:
    """`LoginPage.open` under a renamed class becomes `LoginPageObject.open`."""
    head, _, tail = name.partition(".")
    return f"{renamed[head]}.{tail}" if tail and head in renamed else name


# --- the TODO ledger ---------------------------------------------------------


def clean(text: str) -> str:
    """One TODO line, stripped of the noise that makes two copies look different.

    Comment markers, the `TODO(review)` word itself and a leading `path.ts:` are
    all packaging. What is left is the task, and two tasks are the same task
    when what is left is the same.
    """
    text = COMMENT_MARK.sub("", text.strip()).strip()
    text = PATH_PREFIX.sub("", text)
    text = re.sub(r"^" + re.escape(TODO_MARK) + r"\s*[:\-]?\s*", "", text)
    return re.sub(r"\s+", " ", text).strip(" -")


def todo_blocks(text: str) -> list[tuple[int, str]]:
    """Every TODO(review) in one file: (line number, the whole comment).

    Whole comment, not whole line — a task long enough to be worth writing down
    is usually long enough to wrap, and half a sentence does not match the same
    sentence reported in full, which is how one task becomes two entries.
    """
    lines = text.splitlines()
    blocks, index = [], 0
    while index < len(lines):
        line = lines[index]
        if TODO_MARK not in line:
            index += 1
            continue
        start, parts = index + 1, [line[line.index(TODO_MARK):]]
        index += 1
        while index < len(lines):
            follow = lines[index].strip()
            if TODO_MARK in follow or not CONTINUATION.match(follow):
                break
            parts.append(follow)
            index += 1
        blocks.append((start, " ".join(filter(None, (clean(part) for part in parts)))))
    return blocks


def tidy(places: list[str]) -> tuple[str, ...]:
    """`file:line` beats a bare `file` for the same file — it is the same sighting."""
    located = {place.split(":")[0] for place in places if ":" in place}
    return tuple(place for place in places if ":" in place or place not in located)


def merge(entries: list[TodoEntry]) -> list[TodoEntry]:
    """Fold every task that is contained in a longer one into that longer one.

    The model reports a task in full and writes an abbreviated version of it
    into the code (or the other way round). Exact-match de-duplication misses
    that pair, so containment is the rule: the fuller wording wins and both
    sightings are kept.
    """
    position = {entry.text: number for number, entry in enumerate(entries)}
    kept: list[TodoEntry] = []
    for entry in sorted(entries, key=lambda item: len(item.text), reverse=True):
        for number, other in enumerate(kept):
            if entry.text.lower() in other.text.lower():
                kept[number] = TodoEntry(other.text, tidy([*other.places, *entry.places]))
                break
        else:
            kept.append(TodoEntry(entry.text, tidy(list(entry.places))))
    return sorted(kept, key=lambda entry: position[entry.text])


def todo_ledger(out_root: Path, outcomes: list) -> list[TodoEntry]:
    """Every TODO(review) in the tree, de-duplicated, with every place it appears.

    Two sources, on purpose: the comments actually written in the code (which is
    what a reader will hit, with a line number) and the `todos` the model
    reported (which is what the playbook asked it to list). A task in the code
    but not the list, or the reverse, is worth knowing about — and the same task
    in both, however it was worded, is one entry.
    """
    found: dict[str, list[str]] = {}
    texts: dict[str, str] = {}

    def add(text: str, place: str) -> None:
        key = text.lower()
        if not key:
            return
        texts.setdefault(key, text)
        places = found.setdefault(key, [])
        if place not in places:
            places.append(place)

    for outcome in outcomes:
        target = Path(outcome.written) if outcome.written else None
        if target and target.exists():
            for number, text in todo_blocks(target.read_text(encoding="utf-8")):
                add(text, f"{outcome.path}:{number}")
        for todo in outcome.todos:
            add(clean(todo), outcome.path)
    return merge([TodoEntry(text=texts[key], places=tuple(places))
                  for key, places in found.items()])


# --- the scorecard -----------------------------------------------------------


def scorecard(outcomes: list, manifest, assembly_parts: dict) -> dict:
    """The suite as numbers: statuses, per-gate tallies, laps, and the tree verdict."""
    counts = {"passed": 0, "needs-review": 0, "refused": 0, "failed": 0}
    gates: dict[str, dict[str, int]] = {}
    for outcome in outcomes:
        counts[outcome.status] = counts.get(outcome.status, 0) + 1
        for gate, ok in outcome.gates:
            tally = gates.setdefault(gate, {"passed": 0, "of": 0})
            tally["of"] += 1
            tally["passed"] += int(ok)
    return {
        "files": len(outcomes),
        "statuses": counts,
        "gates": gates,
        "attempts": sum(o.attempts for o in outcomes),
        "waves": len(manifest.waves) if manifest else 0,
        "copied": manifest.counts()["copy"] if manifest else 0,
        "skipped": manifest.counts()["skip"] if manifest else 0,
        **assembly_parts,
    }


def owned_findings(assembly) -> tuple[tuple, tuple]:
    """(errors this run answers for, errors in files it carried across unconverted).

    Fails **closed**. An assembly with no split — one built by hand, or by an
    older graph — has not told us that any finding belongs to somebody else, and
    the safe reading of silence on a quality gate is that they are all ours. The
    opposite default would turn a missing key into a green tree.
    """
    split = getattr(assembly, "split", None) or {}
    if not split:
        return tuple(assembly.tree.findings if assembly.tree else ()), ()
    return (tuple(split.get("converted", ())) + tuple(split.get("companion", ())),
            tuple(split.get("unconverted", ())))


def split_tree_findings(tree, outcomes: list, manifest) -> dict:
    """Sort whole-tree errors by whose fault they can be.

    The tree is compiled as one project, so its errors land in whatever file
    `tsc` was reading — and that file is very often one this run never touched.
    A suite past the demo's cap carries Selenium files across unconverted; they
    cannot compile in a Playwright sandbox, and saying "the tree does NOT
    compile" because of them blames the converter for its own input.

    Three buckets, because two would lie in the other direction:

      converted   errors in a file this run produced. The only number that is
                  a verdict on the conversion, and the only one that goes red.
      unconverted Selenium the run carried across untouched. Expected: it never
                  claimed to be Playwright, and the sandbox has no Selenium.
      companion   everything else carried across — a file with no Selenium left
                  in it that still does not compile. This is the interesting
                  bucket: a caller whose companion's API just moved under it.
                  Not counted against the conversion, never hidden either.
    """
    converted = {o.path for o in outcomes}
    in_tree = {f.path for f in (manifest.files if manifest else ())}
    selenium_left = {f.path for f in (manifest.files if manifest else ())
                     if f.path not in converted
                     and getattr(f.classification, "automation", "") == "selenium"}
    buckets: dict[str, list] = {"converted": [], "unconverted": [], "companion": [],
                                "dependency": []}
    # Both lists: the compile gate keeps the errors it excused (a package the
    # sandbox never installed) out of `findings` so the critic is not handed
    # work it cannot do, but they are still true and this report still shows
    # them — sorted into the same buckets, by the same rules, below.
    reported = ([*tree.findings, *tree.excused] if tree else ())
    for finding in reported:
        # Order matters. For a file this run openly declined to convert, "it is
        # still Selenium" explains every error it has — the missing
        # `selenium-webdriver` module included — and explains it better than
        # "the sandbox lacks a package". Everywhere else, a module that is not
        # in this folder is a dependency we were never going to have.
        if finding.file in selenium_left:
            where = "unconverted"
        elif missing_dependency(finding, in_tree):
            where = "dependency"
        else:
            where = "converted" if finding.file in converted else "companion"
        buckets[where].append(finding)
    return buckets


def assemble(root: Path, out_root: Path, manifest, outcomes: list) -> Assembly:
    """The four facts, in the order they are cheapest to trust.

    Called from the suite graph's `finish` node, so the whole-tree compile is
    part of the traced run rather than something the CLI does afterwards — the
    graph produced the tree, the graph says whether it holds together.
    """
    tree_files = read_tree(out_root)
    tree, tree_error = compile_tree(tree_files)
    built, notes = ledgers(root, out_root, outcomes)
    todos = todo_ledger(out_root, outcomes)
    split = split_tree_findings(tree, outcomes, manifest)
    card = scorecard(outcomes, manifest, {
        "tree_files": len(tree_files),
        # Green when nothing the run produced is broken. Errors in files it
        # carried across untouched are reported in their own right below, and
        # are not a verdict on a conversion that never happened.
        "tree_compiles": tree is not None and not split["converted"] and not split["companion"],
        "tree_errors": len(tree.findings) + len(tree.excused) if tree else 0,
        "tree_errors_converted": len(split["converted"]),
        "tree_errors_unconverted": len(split["unconverted"]),
        "tree_errors_companion": len(split["companion"]),
        "tree_errors_dependency": len(split["dependency"]),
        "todos": len(todos),
        "api": {verdict: sum(ledger.count(verdict) for ledger in built)
                for verdict in ("kept", "renamed", "removed")},
        "unexplained_removals": sum(ledger.unexplained for ledger in built),
    })
    return Assembly(tree=tree, tree_error=tree_error, files=len(tree_files), ledgers=tuple(built),
                    todos=tuple(todos), scorecard=card, notes=tuple(notes),
                    split=({k: tuple(v) for k, v in split.items()}))


# --- the report --------------------------------------------------------------


def cell(text: str) -> str:
    """Anything going into a markdown table cell: no pipes, no newlines."""
    return str(text).replace("|", "\\|").replace("\n", " ").strip()


def table(headers: list[str], rows: list[list[str]]) -> list[str]:
    """A markdown table, or nothing at all when there are no rows to put in it."""
    if not rows:
        return []
    return ["| " + " | ".join(headers) + " |",
            "| " + " | ".join("---" for _ in headers) + " |",
            *("| " + " | ".join(cell(value) for value in row) + " |" for row in rows), ""]


def headline(outcomes: list, assembly: Assembly) -> str:
    """The one sentence somebody reads before deciding whether to read the rest."""
    counts = assembly.scorecard.get("statuses", {})
    parts = [f"{len(outcomes)} file(s) converted"]
    parts.append(", ".join(f"{n} {name}" for name, n in counts.items() if n))
    owned, carried_findings = owned_findings(assembly)
    mine, carried = len(owned), len(carried_findings)
    if assembly.tree is None:
        parts.append("the whole-tree compile did not run")
    elif assembly.tree.passed:
        parts.append("the tree compiles as one project")
    elif not mine:
        # Everything tsc complained about is input this run carried across
        # untouched. Saying "the tree does NOT compile" here reads as a verdict
        # on the conversion, and it is not one.
        parts.append(f"the converted files compile ({carried} error(s) in files "
                     "carried across unconverted)")
    else:
        parts.append(f"the converted files do NOT compile ({mine} error(s))")
    todos = len(assembly.todos)
    parts.append("no open TODOs" if not todos else f"{todos} open TODO(review) task(s)")
    return " · ".join(parts)


def render(root: Path, out_root: Path, manifest, outcomes: list, assembly: Assembly,
           elapsed: float = 0.0, models: dict | None = None, usage: dict | None = None,
           when: datetime | None = None) -> str:
    """The whole report as markdown, written for the person who has to trust the output.

    Order is deliberate: the verdict first, then the fact that decides it (does
    the tree build), then the per-file detail, then what was lost, then what is
    still open. Nothing here re-runs anything — every number was gathered by
    `assemble`, and this function only lays it out.
    """
    models = models or {}
    when = when or datetime.now(timezone.utc)
    card = assembly.scorecard
    lines = [f"# Conversion report — {Path(root).name}", "",
             f"> **{headline(outcomes, assembly)}**", ""]

    facts = [["Source", f"`{root}`"], ["Output", f"`{out_root}`"],
             ["Converted", when.strftime("%Y-%m-%d %H:%M UTC")]]
    if models.get("actor"):
        critic = models.get("critic") or models["actor"]
        facts.append(["Models", f"`{models['actor']}` · critic `{critic}`"
                      if critic != models["actor"] else f"`{models['actor']}`"])
    facts.append(["Waves", f"{card.get('waves', 0)} · {card.get('attempts', 0)} attempt(s) in total"])
    if elapsed:
        facts.append(["Wall clock", f"{elapsed:.1f}s"])
    if usage:
        facts.append(["Tokens", ", ".join(f"{k.replace('_', ' ')} {v:,}"
                                          for k, v in sorted(usage.items()) if isinstance(v, int))])
    lines += table(["", ""], facts)

    lines += ["## 1. Does the tree compile as one project?", ""]
    # The errors this run is answerable for: files it produced, plus files it
    # left alone that stopped compiling anyway. Selenium it never converted is
    # counted separately — see `split_tree_findings`.
    mine_findings, stale = owned_findings(assembly)
    absent = tuple(assembly.split.get("dependency", ()))
    if assembly.tree is None:
        lines += [f"**Unknown** — {assembly.tree_error}. Every other number below still holds; "
                  "this one gate could not be run.", ""]
    elif assembly.tree.passed or not mine_findings:
        lines += [f"**Yes.** `tsc --noEmit` over all {assembly.files} TypeScript file(s) in the "
                  "output at once — converted files, copied support files, and the imports "
                  "between them — reports no errors.", "",
                  "This is the check no per-file gate can do: a page object is compiled here "
                  "against *every* spec that calls it, not just the one it was converted with.", ""]
        if stale:
            # A pass with an asterisk, and the asterisk is worth a paragraph:
            # tsc did report errors, they are simply all in Selenium this run
            # never claimed to convert.
            files = sorted({f.file for f in stale})
            lines += [f"`tsc` did report {len(stale)} error(s), and every one of them is in a "
                      f"file carried across **unconverted** ({len(files)}: "
                      + ", ".join(f"`{f}`" for f in files[:6])
                      + (", …" if len(files) > 6 else "") + "). Those files are still Selenium "
                      "and there is no Selenium in a Playwright project, so they cannot compile "
                      "and were never expected to. Convert them and the errors go with them.", ""]
    else:
        lines += [f"**No.** `tsc --noEmit` over all {assembly.files} file(s) reports "
                  f"{len(mine_findings)} error(s) that this run is answerable for. The per-file "
                  "gates passed because each file was compiled only against the companions it "
                  "imported; these are the problems that only exist between files.", ""]
        lines += table(["file", "line", "code", "error"],
                       [[f.file, f.line or "", f.code, f.message] for f in mine_findings[:40]])
        if len(mine_findings) > 40:
            lines += [f"…and {len(mine_findings) - 40} more.", ""]
        if stale:
            lines += [f"A further {len(stale)} error(s) are in files carried across "
                      "**unconverted** — still Selenium, so they cannot compile in a Playwright "
                      "project. They are not counted above.", ""]

    if absent:
        packages = sorted({m.group(1) for m in
                           (MISSING_MODULE.search(f.message or "") for f in absent) if m})
        lines += [f"A further {len(absent)} error(s) come from packages this sandbox does not "
                  "install — it carries TypeScript and Playwright and nothing else, so an import "
                  "of "
                  + ", ".join(f"`{p}`" for p in packages[:8])
                  + (", …" if len(packages) > 8 else "")
                  + " cannot resolve here and would resolve in your own checkout. Not counted "
                    "against the conversion.", ""]

    lines += ["## 2. Scorecard", ""]
    lines += table(["wave", "file", "result", "laps", "gates", "critic", "TODOs", "secs"],
                   [[o.wave, f"`{o.path}`", o.status, o.attempts, gate_score(o),
                     o.critic or "—", len(o.todos), f"{o.seconds:.0f}"] for o in outcomes])
    gates = card.get("gates", {})
    if gates:
        lines += ["Per gate, across every file that reached it: "
                  + " · ".join(f"**{gate}** {tally['passed']}/{tally['of']}"
                               for gate, tally in gates.items()) + ".", ""]
    reasons = [[f"`{o.path}`", o.status, o.reason] for o in outcomes if o.status != "passed"]
    if reasons:
        lines += ["Why a file is not a plain pass:", ""] + table(["file", "result", "reason"], reasons)

    lines += ["## 3. What was not converted", ""]
    # The demo's size cap, if one bit. It belongs at the top of this section
    # rather than in a footnote: the reader is looking at a table of their own
    # files marked "copied unchanged" and is owed the reason before the list,
    # along with the way to convert them anyway.
    capped = [n for n in (manifest.notes if manifest else ()) if CAP_INVITATION in n]
    lines += [*(f"> {note}\n" for note in capped)]
    carried = [[f"`{f.path}`", "copied unchanged", f.reason] for f in (manifest.files if manifest else ())
               if f.action == "copy"]
    left = [[f"`{f.path}`", "not converted", f.reason] for f in (manifest.files if manifest else ())
            if f.action == "skip"]
    if carried or left:
        lines += table(["file", "what happened", "why"], carried + left)
    else:
        lines += ["Every source file in the folder was converted.", ""]

    lines += ["## 4. Parity ledger", "",
              "What each source file exposed to the rest of the suite — class members and "
              "exported names — and its tests, next to what came back. A **rename** is a guess "
              "from name similarity; check it. A **removal with no reason** is the line to read "
              "first: nothing in the model's own notes explains where it went.", ""]
    api = card.get("api", {})
    lines += [f"Across the suite: **{api.get('kept', 0)} kept**, {api.get('renamed', 0)} likely "
              f"renamed, **{api.get('removed', 0)} removed** "
              f"({card.get('unexplained_removals', 0)} of them unexplained).", ""]
    # A file with nothing to report gets one line, not a section: the point of
    # the ledger is to make the exceptions easy to find.
    quiet = [l for l in assembly.ledgers if not l.note and not l.losses and not l.added]
    if quiet:
        lines += ["Unchanged surface — every public name and test survived, under the same name: "
                  + ", ".join(f"`{l.path}` ({l.count('kept')})" for l in quiet), ""]
    untouched = {l.path for l in quiet}
    for ledger in assembly.ledgers:
        if ledger.path in untouched:
            continue
        lines += [f"### `{ledger.path}`", ""]
        if ledger.note:
            lines += [f"No comparison: {ledger.note}", ""]
            continue
        lines += [f"kept {ledger.count('kept')} · renamed {ledger.count('renamed')} · "
                  f"removed {ledger.count('removed')}", ""]
        losses = [[c.kind, c.name, c.verdict, c.counterpart or "—",
                   c.reason or ("—" if c.verdict == "renamed" else "**no reason given**")]
                  for c in ledger.losses]
        if losses:
            lines += table(["kind", "in the source", "verdict", "now called", "the model's reason"], losses)
        if ledger.added:
            lines += ["New in the conversion: " + ", ".join(f"`{name}`" for name in ledger.added), ""]

    lines += ["## 5. TODO(review) ledger", ""]
    if not assembly.todos:
        lines += ["No file carries an open `TODO(review)`.", ""]
    else:
        lines += [f"{len(assembly.todos)} distinct task(s), gathered from the comments in the code "
                  "and from what each conversion reported. The same task reported by two files — a "
                  "spec repeating the page object's TODO it was given as context — is one line here.",
                  ""]
        lines += table(["#", "task", "where"],
                       [[n, todo.text, ", ".join(f"`{place}`" for place in todo.places)]
                        for n, todo in enumerate(assembly.todos, 1)])

    if assembly.notes:
        lines += ["## 6. Notes on this report", ""] + [f"- {note}" for note in assembly.notes] + [""]

    lines += ["## How to read this", "",
              "- **Gates are deterministic.** compile and lint run the pinned TypeScript and ESLint; "
              "residue is a text search for Selenium leftovers; parity compares tests and assertions "
              "statically. Nothing here executes the converted code, and no browser was opened.",
              "- **Parity is syntactic.** A kept test with the same assertion count is not proof that "
              "it asserts the same thing.",
              "- **Renames are inferred by name similarity**, not by reading the code.",
              "- **A passing file is a reviewed draft, not a merged one.** The TODO ledger above is "
              "the shortest path through what still needs a human.", ""]
    return "\n".join(lines)


def gate_score(outcome) -> str:
    """`4/4`, or `2/4 (lint, parity)` — the same shorthand the CLI table uses."""
    if not outcome.gates:
        return "—"
    failed = [gate for gate, ok in outcome.gates if not ok]
    score = f"{len(outcome.gates) - len(failed)}/{len(outcome.gates)}"
    return score if not failed else f"{score} ({', '.join(failed)})"


def report_json(run: dict, assembly: Assembly) -> dict:
    """The 9.2 run document plus the four assembled facts, under a new schema name.

    A superset, deliberately: everything that read `s2p.suite-run/v1` still finds
    its keys where they were, and the assembled sections are additions rather
    than a reshuffle.
    """
    return {**run, "schema": REPORT_SCHEMA,
            "report": assembly.report_path,
            "tree": {
                "files": assembly.files,
                "compiles": assembly.compiles,
                "error": assembly.tree_error,
                "findings": [f.model_dump() for f in assembly.tree_findings],
            },
            "scorecard": assembly.scorecard,
            "parity": [{"path": ledger.path, "note": ledger.note,
                        "kept": ledger.count("kept"),
                        "changes": [{"kind": c.kind, "name": c.name, "verdict": c.verdict,
                                     "now": c.counterpart, "reason": c.reason}
                                    for c in ledger.losses],
                        "added": list(ledger.added)} for ledger in assembly.ledgers],
            "todos": [{"text": todo.text, "places": list(todo.places), "files": list(todo.files)}
                      for todo in assembly.todos],
            "notes": list(assembly.notes)}
