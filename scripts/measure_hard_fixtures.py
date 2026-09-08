"""Phase 11.1: measure the hard-case fixtures and write their evidence file.

    uv run python scripts/measure_hard_fixtures.py

Runs the four static gates over the whole hard-case golden tree, then runs both
suites in a real browser, then binds every measurement to the exact text it was
measured against. A later edit to any fixture changes its hash, and the dataset
builder refuses the stale evidence rather than inheriting an old browser pass.

No model is called and nothing is uploaded. Requires network access to
the-internet.herokuapp.com and an installed Chrome plus Playwright browsers.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from selenium2playwright.eval_hardcases import CASES, PLANNED_BROWSER_TEST_COUNTS
from selenium2playwright.validators.compile import compile_check
from selenium2playwright.validators.lint import lint_check
from selenium2playwright.validators.parity import parity_check
from selenium2playwright.validators.residue import residue_check

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"
SOURCE_DIR = SAMPLES / "selenium-hard-suite"
GOLDEN_DIR = SAMPLES / "playwright-hard-golden"
EVIDENCE = ROOT / "docs/evaluation-hard-fixture-evidence.json"
ARTIFACTS = ROOT / "out/11.1"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_suite(root: Path) -> dict[str, str]:
    """Every .ts file in a suite, keyed by the relative path both suites share."""
    return {str(path.relative_to(root)): path.read_text(encoding="utf-8")
            for path in sorted(root.rglob("*.ts"))}


def run(command: list[str], cwd: Path) -> subprocess.CompletedProcess:
    """Run a suite and keep going on a non-zero exit; the JSON report is the evidence."""
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True)


def static_gates(source: dict[str, str], golden: dict[str, str]) -> dict:
    """The same four gates the converter must pass, run over the reference tree."""
    reports = {
        "compile": compile_check(golden),
        "residue": residue_check(golden),
        "lint": lint_check(golden),
        "parity": parity_check(source, golden),
    }
    scope = f"all {len(golden)} hard-case golden files together"
    return {name: {"passed": report.passed,
                   "findings": [finding.__dict__ for finding in report.findings],
                   "scope": scope}
            for name, report in reports.items()}


def mocha_results(report: dict) -> dict[str, list[dict]]:
    """Group passing Mocha tests by the spec file that declared them."""
    grouped: dict[str, list[dict]] = {}
    for test in report["tests"]:
        relative = str(Path(test["file"]).relative_to(SOURCE_DIR))
        grouped.setdefault(relative, []).append(
            {"name": test["title"],
             "status": "passed" if not test["err"] else "failed",
             "duration_ms": test["duration"]})
    return grouped


def playwright_results(report: dict) -> dict[str, list[dict]]:
    """Group Playwright specs by file, preserving declaration order.

    A describe block is a nested suite in this report, so the specs that matter
    are one level below the file entry; walk the tree rather than the top level.
    """
    grouped: dict[str, list[dict]] = {}

    def walk(suite: dict) -> None:
        for spec in suite.get("specs", []):
            relative = str(Path("tests") / Path(spec["file"]).name)
            result = spec["tests"][0]["results"][0]
            grouped.setdefault(relative, []).append(
                {"name": spec["title"], "status": result["status"],
                 "duration_ms": round(result["duration"])})
        for child in suite.get("suites", []):
            walk(child)

    for suite in report["suites"]:
        walk(suite)
    return grouped


def main() -> int:
    source = read_suite(SOURCE_DIR)
    golden = read_suite(GOLDEN_DIR)
    if set(source) != set(golden):
        raise ValueError("Both suites must contain the same relative paths")
    ARTIFACTS.mkdir(parents=True, exist_ok=True)

    gates = static_gates(source, golden)
    (ARTIFACTS / "static-gates.json").write_text(json.dumps(gates, indent=2) + "\n", encoding="utf-8")

    selenium = run(["npx", "mocha", "--require", "tsx/cjs", "--reporter", "json",
                    "selenium-hard-suite/tests/**/*.spec.ts"], SAMPLES)
    selenium_report = json.loads(selenium.stdout)
    (ARTIFACTS / "selenium.json").write_text(selenium.stdout, encoding="utf-8")

    playwright = run(["npx", "playwright", "test", "--config", "playwright.hard.config.ts",
                      "--workers", "1", "--retries", "0", "--reporter", "json"], SAMPLES)
    playwright_report = json.loads(playwright.stdout)
    (ARTIFACTS / "playwright.json").write_text(playwright.stdout, encoding="utf-8")

    by_source = mocha_results(selenium_report)
    by_reference = playwright_results(playwright_report)

    cases = {}
    for case in CASES:
        test_id = case.case_id if case.case_id in PLANNED_BROWSER_TEST_COUNTS else case.browser_evidence_from
        test_path = next(item.path for item in CASES if item.case_id == test_id)
        cases[case.case_id] = {
            "source_sha256": sha256_text(source[case.path]),
            "reference_sha256": sha256_text(golden[case.path]),
            "browser_test_case_id": test_id,
            "source_browser": by_source[test_path],
            "reference_browser": by_reference[test_path],
        }

    versions = json.loads(subprocess.check_output(
        ["npm", "ls", "--depth", "0", "--json"], cwd=SAMPLES, text=True))["dependencies"]
    evidence = {
        "schema_version": 1,
        "scope": ("Hard-case source fixtures and their independently authored goldens; "
                  "not converter outputs and not a scored experiment."),
        "measured_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "tools": {name: versions[name]["version"] for name in sorted(
            ("@playwright/test", "selenium-webdriver", "typescript", "mocha"))}
        | {"node": subprocess.check_output(["node", "--version"], text=True).strip()},
        "browser_settings": {"source": "headless Chrome", "reference": "headless Chromium",
                             "playwright_workers": 1, "playwright_retries": 0},
        "browser_totals": {"source": selenium_report["stats"],
                           "reference": playwright_report["stats"]},
        "static_gates": gates,
        "artifacts": {"source": "out/11.1/selenium.json", "reference": "out/11.1/playwright.json",
                      "static": "out/11.1/static-gates.json"},
        "cases": cases,
    }
    EVIDENCE.write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"static_gates": {name: gate["passed"] for name, gate in gates.items()},
                      "source_browser": selenium_report["stats"],
                      "reference_browser": playwright_report["stats"],
                      "evidence": str(EVIDENCE.relative_to(ROOT))}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
