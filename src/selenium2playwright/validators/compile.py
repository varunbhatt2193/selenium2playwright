"""Gate 1 — does the generated TypeScript compile? (taxonomy T1, T2, and T3 via imports)

    uv run python -m selenium2playwright.validators.compile samples/playwright-golden/**/*.ts

Runs `tsc --noEmit` from sandbox/ (pinned compiler, NO selenium-webdriver) on
a private work/<run>/ copy of the files, then turns every error line into a
Finding. The model never sees the compiler directly — it sees this report.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from selenium2playwright.env import SANDBOX
from selenium2playwright.schemas import Finding, ValidationReport

WORK = SANDBOX / "work"
TSC = SANDBOX / "node_modules" / ".bin" / "tsc"

# `work/ab12/tests/login.spec.ts(14,5): error TS2551: Property 'fil' does not exist...`
TSC_LINE = re.compile(r"^(?P<file>.+?)\((?P<line>\d+),(?P<col>\d+)\): error (?P<code>TS\d+): (?P<msg>.*)$")


# `@lib/test.metadata`, `@/pages/index`, `~/config/env` — an import that is not
# relative and not a package, but a path alias the project defined in its own
# tsconfig. The first segment is the alias; the rest is a path under it.
ALIAS_IMPORT = re.compile(
    r"""(?:\bfrom\s*|\bimport\s*|\brequire\s*\(\s*|\bimport\s*\(\s*)['"]((?:@|~)[^'"]*)['"]""")

# The extensions a specifier may be leaving off, longest first so `/index.ts`
# is tried before `.ts` turns `pages` into `pages.ts`.
ALIAS_ENDINGS = ("", ".ts", ".tsx", ".d.ts", "/index.ts", "/index.tsx")


# `Cannot find module 'zod' or its corresponding type declarations.`
MISSING_MODULE = re.compile(r"""Cannot find module ['"]([^'"]+)['"]""")

# `Cannot find name 'context'.`
MISSING_NAME = re.compile(r"""Cannot find name ['"]([^'"]+)['"]""")

# Globals a test runner injects with no import, which exist only if its types
# are installed. They are not, deliberately, so a suite that still contains its
# Mocha specs reports them by the dozen.
#
# `expect` and `test` are pointedly NOT here. Those come from
# `@playwright/test`, which *is* installed, so a converted file that reports
# them undefined has a real bug — forgetting the import is exactly the kind of
# thing this gate exists to catch, and excusing it would be a hole.
#
# Everything listed is Mocha, Jest or Cypress, and the residue gate refuses all
# of it in converted output. That is what makes excusing it here safe: the
# question is owned, just by the gate whose question it is.
RUNNER_GLOBALS = frozenset({
    "describe", "it", "context", "specify", "suite",
    "before", "after", "beforeEach", "afterEach", "beforeAll", "afterAll",
    "jest", "cy", "chai",
})


def missing_dependency(finding, in_tree: set[str]) -> bool:
    """Is this finding about something the sandbox was never going to have?

    Two shapes: a module that is not installed (TS2307), and a global that a
    test runner would have injected if its types were (TS2304).

    The sandbox carries TypeScript and Playwright and nothing else, deliberately
    — it is how the residue gate can promise there is no Selenium to fall back
    on. The cost is that a suite importing `zod` or `mysql2` reports errors that
    no amount of converting better would fix, and they are as often in a
    companion file as in the converted one.

    A specifier that points at a file in this folder is never a dependency: a
    broken relative import, or an alias that resolves to nothing, is a real
    finding and stays one.
    """
    code = getattr(finding, "code", "")
    message = getattr(finding, "message", "") or ""
    if code == "TS2304":
        name = MISSING_NAME.search(message)
        return bool(name and name.group(1) in RUNNER_GLOBALS)
    if code != "TS2307":
        return False
    match = MISSING_MODULE.search(message)
    if not match:
        return False
    specifier = match.group(1)
    if specifier.startswith("."):
        return False
    head, _, rest = specifier.partition("/")
    if head.startswith(("@", "~")) and rest:
        target = rest if head in ("@", "~") else f"{head.lstrip('@~')}/{rest}"
        if any(f"{target}{end}" in in_tree
               for end in ("", ".ts", ".tsx", "/index.ts", "/index.tsx")):
            return False
    return True


def alias_paths(files: dict[str, str]) -> dict[str, list[str]]:
    """The `paths` a tsconfig needs so this tree's own aliases resolve.

    A real repository rarely imports by relative path. It writes `@lib/x` and
    defines what `@lib` means in its own tsconfig — which never reaches us: the
    scanner only collects source files, and the sandbox compiles against a
    frozen config with no `paths` at all. Every aliased import then fails
    TS2307, the compile gate fails a file that is perfectly good, and the
    reflection loop spends all three attempts trying to fix an import that was
    already right. One real suite scored 0 of 3 that way, and the model's own
    notes said it had worked out the project was alias-based and restored the
    aliases on purpose.

    So derive them. An alias is only accepted when the tree actually contains
    what it points at, which is what keeps a genuine npm scope out: `@lib/x`
    maps to `lib/x` because `lib/` is right there, and `@playwright/test` maps
    to nothing because there is no `playwright/` in the folder.
    """
    known = set(files)
    dirs = {parent for path in known for parent in Path(path).parents if str(parent) != "."}
    mapping: dict[str, list[str]] = {}
    for source in files.values():
        for specifier in ALIAS_IMPORT.findall(source):
            head, _, rest = specifier.partition("/")
            if not rest:
                continue
            # `@/pages/x` and `~/pages/x` have an empty alias: they mean the root.
            target = rest if head in ("@", "~") else f"{head.lstrip('@~')}/{rest}"
            if not any(target + ending in known for ending in ALIAS_ENDINGS) \
                    and Path(target) not in dirs:
                continue  # points at nothing here — a package, not an alias
            pattern = f"{head}/*"
            resolves_to = "*" if head in ("@", "~") else f"{head.lstrip('@~')}/*"
            mapping[pattern] = [resolves_to]
    return dict(sorted(mapping.items()))


def compile_check(files: dict[str, str], keep: bool = False) -> ValidationReport:
    """files = {relative path: contents}. Relative paths matter: tests import ../pages/X."""
    if not TSC.exists():
        raise RuntimeError(f"{TSC} missing — run `npm install` inside sandbox/ first")
    run_dir = WORK / uuid4().hex[:8]
    try:
        for rel, content in files.items():
            target = run_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        # Both keys are written only when this tree actually uses aliases, so a
        # suite that imports by relative path compiles under exactly the config
        # it did before. `baseUrl` alone would quietly start resolving bare
        # specifiers against the folder, which would hide real broken imports.
        config: dict = {"extends": "../../tsconfig.base.json", "include": ["**/*.ts"]}
        aliases = alias_paths(files)
        if aliases:
            config["compilerOptions"] = {"baseUrl": ".", "paths": aliases}
        (run_dir / "tsconfig.json").write_text(json.dumps(config, indent=2) + "\n",
                                               encoding="utf-8")
        proc = subprocess.run(
            [str(TSC), "-p", str(run_dir / "tsconfig.json"), "--pretty", "false"],
            cwd=SANDBOX, capture_output=True, text=True, timeout=120,
        )
    finally:
        if not keep:
            shutil.rmtree(run_dir, ignore_errors=True)

    findings = parse_tsc_output(proc.stdout, prefix=str(run_dir.relative_to(SANDBOX)) + "/")
    # A file is not badly converted because the folder next to it imports a
    # package this sandbox does not install. Those findings are still reported —
    # `excused` keeps every line tsc printed — they just do not fail the gate,
    # because failing it sends the repair loop off to rewrite correct code, and
    # they are held apart from `findings` because that list is the critic's
    # to-do list: an excused error there reads as work, and gets "revise".
    known = set(files)
    blocking = [f for f in findings if not missing_dependency(f, known)]
    excused = [f for f in findings if missing_dependency(f, known)]
    return ValidationReport(
        gate="compile",
        passed=not blocking,
        findings=blocking,
        excused=excused,
        tool_output=proc.stdout + proc.stderr,
    )


def parse_tsc_output(output: str, prefix: str = "") -> list[Finding]:
    """One Finding per `file(line,col): error TSxxxx: msg`; indented lines continue the last message."""
    findings: list[Finding] = []
    for raw in output.splitlines():
        m = TSC_LINE.match(raw)
        if m:
            file = m["file"].removeprefix(prefix)
            findings.append(Finding(gate="compile", file=file, line=int(m["line"]),
                                    column=int(m["col"]), code=m["code"], message=m["msg"].strip()))
        elif raw.startswith(" ") and findings:  # tsc's multi-line elaboration
            findings[-1].message += " " + raw.strip()
    return findings


def main(paths: list[str]) -> int:
    """Validate files from disk; keys are paths relative to their common folder."""
    base = Path(os.path.commonpath([str(Path(p).resolve().parent) for p in paths]))
    files = {str(Path(p).resolve().relative_to(base)): Path(p).read_text(encoding="utf-8") for p in paths}
    report = compile_check(files)
    print(report.render())
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
