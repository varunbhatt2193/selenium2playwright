"""Gate 2 — did any Selenium / Mocha / chai survive the conversion? (taxonomy T3)

    uv run python -m selenium2playwright.validators.residue out/2.2/**/*.ts

The compiler only complains about residue it cannot resolve. A `driver.` call
on a variable the model re-declared, or a chai `expect(...).to.contain` that
happens to type-check, sails through tsc. This gate is independent of the
compiler and independent of the model: plain patterns over plain lines.

Regex first; AST only when a real file proves regex insufficient (roadmap
policy). Keyed by language so Phase 11 can add Java/Python source-side rules.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from selenium2playwright.schemas import Finding, ValidationReport

# code -> (pattern, message). Order = report order.
RULES: dict[str, list[tuple[str, str, str]]] = {
    "typescript": [
        ("forbidden-import", r"""from\s+['"](selenium-webdriver|chai|mocha|webdriverio|@wdio/[\w-]+)(/[\w-]+)?['"]|require\(['"](selenium-webdriver|chai|mocha)""",
         "imports a Selenium/Mocha/chai module — output must import only @playwright/test"),
        ("selenium-api", r"""\bdriver\.|\bBy\.\w+\(|\buntil\.\w+\(|new\s+Builder\(|\.findElements?\(|\.sendKeys\(|\.switchTo\(\)|\.executeScript\(|\bWebDriver\b|\bWebElement\b""",
         "Selenium WebDriver API left in output"),
        ("mocha-api", r"""(?<![.\w])describe\(|(?<![.\w])it\(|(?<![.\w])before\(|(?<![.\w])after\(|(?<![.\w])beforeEach\(|(?<![.\w])afterEach\(|\bthis\.timeout\(""",
         "Mocha hook/structure left in output — use test.describe / test / test.beforeEach"),
        ("chai-assertion", r"""\)\s*\.to\.(equal|eql|contain|include|be|have|not|deep|match)\b|\bassert\.\w+\(""",
         "chai-style assertion left in output — use Playwright expect"),
    ],
}

COMMENT_LINE = re.compile(r"^\s*(//|/\*|\*)")


def residue_check(files: dict[str, str], language: str = "typescript") -> ValidationReport:
    """files = {relative path: contents}. Same contract as compile_check."""
    findings: list[Finding] = []
    for rel, content in files.items():
        for lineno, line in enumerate(content.splitlines(), start=1):
            if COMMENT_LINE.match(line):
                continue  # a comment *mentioning* driver.wait is not residue
            code_part = line.split("//", 1)[0]  # ignore trailing comments too
            for code, pattern, message in RULES[language]:
                m = re.search(pattern, code_part)
                if m:
                    findings.append(Finding(gate="residue", file=rel, line=lineno, column=m.start() + 1,
                                            code=code, message=f"{message}: `{m.group(0).strip()}`"))
    return ValidationReport(gate="residue", passed=not findings, findings=findings)


def main(paths: list[str]) -> int:
    base = Path(os.path.commonpath([str(Path(p).resolve().parent) for p in paths]))
    files = {str(Path(p).resolve().relative_to(base)): Path(p).read_text(encoding="utf-8") for p in paths}
    report = residue_check(files)
    print(report.render())
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
