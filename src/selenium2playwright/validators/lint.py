"""Gate 3 — typed ESLint: bugs the compiler accepts. (taxonomy T4: missed await)

    uv run python -m selenium2playwright.validators.lint samples/playwright-golden/**/*.ts

`page.getByLabel('x').fill('y')` without `await` type-checks perfectly and
then races. Only a linter that knows the return type is a Promise can flag
it, so this gate runs ESLint with typescript-eslint's type-aware rules
(config: sandbox/eslint.config.mjs) on a private work/<run>/ copy — the same
layout compile.py uses, so `projectService` finds the same tsconfig.

Severity policy: ESLint errors (severity 2) fail the gate; warnings
(severity 1) are reported to the critic but do not fail on their own.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from selenium2playwright.prompts import REPO_ROOT
from selenium2playwright.schemas import Finding, ValidationReport

SANDBOX = REPO_ROOT / "sandbox"
WORK = SANDBOX / "work"
ESLINT = SANDBOX / "node_modules" / ".bin" / "eslint"
CONFIG = SANDBOX / "eslint.config.mjs"

SEVERITY = {1: "warning", 2: "error"}


def lint_check(files: dict[str, str], keep: bool = False) -> ValidationReport:
    """files = {relative path: contents}. Same contract as compile_check."""
    if not ESLINT.exists():
        raise RuntimeError(f"{ESLINT} missing — run `npm install` inside sandbox/ first")
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
            [str(ESLINT), "--config", str(CONFIG), "--format", "json", str(run_dir)],
            cwd=SANDBOX, capture_output=True, text=True, timeout=120,
        )
    finally:
        if not keep:
            shutil.rmtree(run_dir, ignore_errors=True)

    # exit 1 = lint problems (expected); anything else = ESLint itself broke.
    if proc.returncode not in (0, 1) or not proc.stdout.strip():
        raise RuntimeError(f"eslint failed (exit {proc.returncode}):\n{proc.stderr}")

    findings = parse_eslint_json(proc.stdout, base=run_dir)
    return ValidationReport(
        gate="lint",
        passed=not any(f.code.startswith("error/") for f in findings),
        findings=findings,
        tool_output=proc.stdout,
    )


def parse_eslint_json(output: str, base: Path) -> list[Finding]:
    """ESLint's JSON formatter: [{filePath, messages: [{ruleId, severity, line, column, message}]}]."""
    findings: list[Finding] = []
    for entry in json.loads(output):
        rel = str(Path(entry["filePath"]).relative_to(base))
        for m in entry["messages"]:
            # ESLint's own failures (parse error, missing tsconfig) arrive with ruleId=None.
            rule = m.get("ruleId") or "eslint"
            findings.append(Finding(gate="lint", file=rel, line=m.get("line"), column=m.get("column"),
                                    code=f"{SEVERITY.get(m['severity'], 'error')}/{rule}",
                                    message=m["message"].rstrip(".")))
    return findings


def main(paths: list[str]) -> int:
    base = Path(os.path.commonpath([str(Path(p).resolve().parent) for p in paths]))
    files = {str(Path(p).resolve().relative_to(base)): Path(p).read_text(encoding="utf-8") for p in paths}
    report = lint_check(files)
    print(report.render())
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
