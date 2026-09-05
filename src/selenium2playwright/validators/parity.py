"""Gate 4 — catch tests/assertions lost in conversion (taxonomy T5).

    uv run python -m selenium2playwright.validators.parity samples/selenium-suite samples/playwright-golden

Both dictionaries use matching relative paths, as in the other gates. Findings
for losses point into the SOURCE: the missing code has no output location.
Names include enclosing suites; duplicate names are matched in source order.
Counts are syntactic, not proof of equivalent assertions or runtime coverage.
The public-member kept/renamed/removed ledger belongs to suite assembly (9.3).
"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections import defaultdict, deque
from pathlib import Path

from selenium2playwright.prompts import REPO_ROOT
from selenium2playwright.schemas import Finding, ValidationReport

SANDBOX = REPO_ROOT / "sandbox"


def parity_check(source_files: dict[str, str], converted_files: dict[str, str]) -> ValidationReport:
    """Compare static test identities and assertion counts, without executing either side."""
    if not (SANDBOX / "node_modules/typescript/lib/typescript.js").exists():
        raise RuntimeError("TypeScript missing — run `npm install` inside sandbox/ first")
    proc = subprocess.run(
        ["node", str(SANDBOX / "parity.cjs")],
        input=json.dumps([source_files, converted_files]),
        cwd=SANDBOX, capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"parity inventory failed (exit {proc.returncode}):\n{proc.stderr}")
    source, converted = json.loads(proc.stdout)
    findings: list[Finding] = []

    def add(file: str, location: dict, code: str, message: str) -> None:
        findings.append(Finding(gate="parity", file=file, line=location.get("line"),
                                column=location.get("column"), code=code, message=message))

    def compare_assertions(file: str, label: str, before: list, after: list) -> None:
        if len(after) < len(before):
            originals = "; ".join(f"line {a['line']}: {a['text']}" for a in before)
            add(file, before[0], "missing-assertion",
                f"{label}: assertion count dropped from {len(before)} to {len(after)}. "
                f"Source assertions to review: {originals}")

    # Never turn an unparseable or unsupported shape into a green zero count.
    for side, files in (("source", source), ("converted", converted)):
        for file, inventory in files.items():
            for issue in inventory["issues"]:
                add(file, issue, "unverified-parity", f"{side}: {issue['message']}")

    for file, before in source.items():
        after = converted.get(file)
        if after is None:
            add(file, {}, "missing-file", "Source file has no converted counterpart")
            continue
        # A queue preserves duplicate test occurrences; a set would hide losses.
        available = defaultdict(deque)
        for test in after["tests"]:
            available[tuple(test["name"])].append(test)
        occurrences: dict[tuple, int] = defaultdict(int)
        for test in before["tests"]:
            key = tuple(test["name"])
            occurrences[key] += 1
            label = f"test {' > '.join(key)!r} (occurrence {occurrences[key]}; source location)"
            if not available[key]:
                add(file, test, "missing-test", f"Missing {label}")
                continue
            match = available[key].popleft()
            if not test["disabled"] and match["disabled"]:
                add(file, test, "disabled-test", f"Previously active {label} is now skipped or pending")
            compare_assertions(file, label, test["assertions"], match["assertions"])
        compare_assertions(file, "outside test bodies (source location)", before["outside"], after["outside"])

    return ValidationReport(gate="parity", passed=not findings, findings=findings, tool_output=proc.stdout)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare a source/converted file pair or two suite directories")
    parser.add_argument("source", type=Path)
    parser.add_argument("converted", type=Path)
    args = parser.parse_args()
    if args.source.is_file() and args.converted.is_file():
        source = {args.source.name: args.source.read_text(encoding="utf-8")}
        converted = {args.source.name: args.converted.read_text(encoding="utf-8")}
    elif args.source.is_dir() and args.converted.is_dir():
        def read_tree(root: Path) -> dict[str, str]:
            return {p.relative_to(root).as_posix(): p.read_text(encoding="utf-8")
                    for p in sorted(root.rglob("*.ts")) if "node_modules" not in p.relative_to(root).parts}
        source, converted = read_tree(args.source), read_tree(args.converted)
        if not source:
            parser.error("source directory contains no TypeScript files")
    else:
        parser.error("provide two existing files or two existing directories")
    report = parity_check(source, converted)
    print(report.render())
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
