"""Gate 1 — does the generated TypeScript compile? (taxonomy T1, T2, and T3 via imports)

    uv run python -m selenium2playwright.validators.compile samples/playwright-golden/**/*.ts

Runs `tsc --noEmit` from sandbox/ (pinned compiler, NO selenium-webdriver) on
a private work/<run>/ copy of the files, then turns every error line into a
Finding. The model never sees the compiler directly — it sees this report.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from selenium2playwright.prompts import REPO_ROOT
from selenium2playwright.schemas import Finding, ValidationReport

SANDBOX = REPO_ROOT / "sandbox"
WORK = SANDBOX / "work"
TSC = SANDBOX / "node_modules" / ".bin" / "tsc"

# `work/ab12/tests/login.spec.ts(14,5): error TS2551: Property 'fil' does not exist...`
TSC_LINE = re.compile(r"^(?P<file>.+?)\((?P<line>\d+),(?P<col>\d+)\): error (?P<code>TS\d+): (?P<msg>.*)$")


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
        (run_dir / "tsconfig.json").write_text(
            '{ "extends": "../../tsconfig.base.json", "include": ["**/*.ts"] }\n', encoding="utf-8"
        )
        proc = subprocess.run(
            [str(TSC), "-p", str(run_dir / "tsconfig.json"), "--pretty", "false"],
            cwd=SANDBOX, capture_output=True, text=True, timeout=120,
        )
    finally:
        if not keep:
            shutil.rmtree(run_dir, ignore_errors=True)

    findings = parse_tsc_output(proc.stdout, prefix=str(run_dir.relative_to(SANDBOX)) + "/")
    return ValidationReport(
        gate="compile",
        passed=proc.returncode == 0 and not findings,
        findings=findings,
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
