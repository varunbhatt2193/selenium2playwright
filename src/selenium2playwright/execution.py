"""Step 11.2 — the fifth measurement: does the converted code actually run?

The four gates in ``validators/`` read the code. Compile, residue, lint and
parity can all pass while a converted test silently checks nothing — that is the
whole premise of the hard cases. This module runs the code instead, in a real
browser, against a pinned local copy of the demo app.

**This is evaluation-only infrastructure.** plan.md's safety rule is "never
execute user-submitted code; execution evals run only on our own curated dataset
in CI". So nothing here is imported by the graph, the CLI or the deployed
service, and ``tests/test_execution.py`` asserts that it stays that way.

One row is executed by putting the converted file back into the golden tree it
came from and running only the browser test that exercises it. Everything else
in the tree stays golden, so a red result names one file.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from tempfile import mkdtemp
from time import perf_counter, sleep

from selenium2playwright.eval_hardcases import CASES as HARD_CASES
from selenium2playwright.eval_manifest import CASES as BASE_CASES

# The app the fixtures were authored against, and the local copy CI runs.
PUBLIC_BASE_URL = "https://the-internet.herokuapp.com"
LOCAL_BASE_URL = "http://127.0.0.1:7080"

# Pinned by digest, not by tag: "latest" was last pushed in 2020 and could move
# under us tomorrow. This exact image is the app for every execution number in
# docs/phase-11.2-report.md.
APP_IMAGE = ("gprestes/the-internet@sha256:"
             "0c5f3d11d45264ff6363ed48b769d8a67c9fb2eea39cc0e07ead4f639cdacff1")
APP_VERSION = "0.58.0"
APP_CONTAINER_PORT = 5000


@dataclass(frozen=True)
class Suite:
    """One benchmark's runnable side: the golden tree and its measured evidence."""

    name: str
    golden_dir: str
    evidence: str
    cases: tuple

    def path_of(self, case_id: str) -> str:
        """The suite-relative .ts path of one case, by its dataset case_id."""
        for case in self.cases:
            if case.case_id == case_id:
                return case.path
        raise KeyError(f"{self.name} suite has no case {case_id!r}")


SUITES = {
    "base": Suite("base", "playwright-golden", "docs/evaluation-fixture-evidence.json", BASE_CASES),
    "hard": Suite("hard", "playwright-hard-golden", "docs/evaluation-hard-fixture-evidence.json", HARD_CASES),
}

# The pinned image is the-internet 0.58.0, built in 2020. Production has moved
# since. Every entry below was MEASURED, never assumed: run the same untouched
# golden file against both base URLs and the difference is what remains.
# A divergent test is excluded from pass/fail and named in every report, and the
# golden gate fails if one of them starts PASSING — that means the app changed
# and this list is stale.
KNOWN_APP_DIVERGENCES = {
    ("base", "tests/alerts.spec.ts", "accepts a JavaScript alert"): {
        "pinned_app": "You successfuly clicked an alert",
        "production_app": "You successfully clicked an alert",
        "why": ("Upstream fixed this spelling after the 2020 image was built. The golden "
                "asserts production's text, so it cannot pass against the pinned app."),
    },
}


# Playwright colours its failure messages; a report file should not carry escapes.
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def plain(text: str) -> str:
    """Readable failure text: no terminal colour codes, no trailing whitespace."""
    return ANSI.sub("", text or "").strip()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def suite_for(name: str) -> Suite:
    if name not in SUITES:
        raise ValueError(f"Unknown suite {name!r}; expected one of {sorted(SUITES)}")
    return SUITES[name]


def benchmark_of(plan: dict) -> str:
    """Which benchmark a saved experiment ran, including plans written before the key existed."""
    named = plan.get("metadata", {}).get("benchmark")
    if named:
        return named
    return "hard" if "-hard-" in plan.get("dataset_name", "") else "base"


def golden_tree(samples_root: Path, suite: Suite) -> dict[str, str]:
    """Every golden .ts file of a suite, keyed by the relative path its imports assume."""
    root = samples_root / suite.golden_dir
    return {path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
            for path in sorted(root.rglob("*.ts"))}


def browser_evidence(root: Path, suite: Suite, case_id: str) -> dict:
    """The measured golden run for one case: which spec exercises it, and the test names.

    Both come from the same fixture-evidence file the dataset row was built
    from, so "what should have run" is the thing that was measured in Step 6.1 /
    11.1a — not a list retyped here.
    """
    evidence = json.loads((root / suite.evidence).read_text(encoding="utf-8"))
    checked = evidence["cases"][case_id]
    spec = suite.path_of(checked["browser_test_case_id"])
    return {"spec": spec, "test_case_id": checked["browser_test_case_id"],
            "expected": [test["name"] for test in checked["reference_browser"]],
            "measured_at_utc": evidence["measured_at_utc"]}


def rewrite_base_url(text: str, base_url: str) -> tuple[str, int]:
    """Point hard-coded production URLs at the app actually being run.

    Converted files usually navigate with a relative path and let baseURL decide
    (playbook rule), but not always — a few saved runs wrote the absolute URL.
    Executing those against production would quietly measure a different app, so
    the rewrite happens here, is counted, and is reported per row.
    """
    if base_url.rstrip("/") == PUBLIC_BASE_URL:
        return text, 0
    return text.replace(PUBLIC_BASE_URL, base_url.rstrip("/")), text.count(PUBLIC_BASE_URL)


def app_status(base_url: str, timeout: float = 5.0) -> dict:
    """One HTTP GET: is something serving the demo app at this URL right now?"""
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/", timeout=timeout) as response:
            body = response.read(4096).decode("utf-8", "replace")
            return {"reachable": response.status == 200 and "The Internet" in body,
                    "status": response.status, "url": base_url}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"reachable": False, "status": None, "url": base_url, "error": str(exc)}


def wait_for_app(base_url: str, timeout: float = 60.0) -> dict:
    """Poll until the app answers, so a container that is still booting is not a failure."""
    deadline = perf_counter() + timeout
    status = app_status(base_url)
    while not status["reachable"] and perf_counter() < deadline:
        sleep(1.0)
        status = app_status(base_url)
    return status


CONFIG = """import {{ defineConfig }} from "@playwright/test";

// Written by selenium2playwright's execution eval (step 11.2). testDir is the
// workspace root because the tree keeps its suite-relative layout: tests import
// ../pages/X exactly as they do in samples/.
export default defineConfig({{
  testDir: ".",
  outputDir: "./test-results",
  use: {{ baseURL: {base_url} }},
}});
"""


def _write_tree(workspace: Path, tree: dict[str, str], base_url: str) -> int:
    """Materialize one runnable suite; return how many hard-coded URLs were rewritten."""
    rewrites = 0
    for relative, code in sorted(tree.items()):
        path = PurePosixPath(relative)
        if path.is_absolute() or ".." in path.parts or path.suffix != ".ts":
            raise ValueError(f"Expected a canonical suite-relative .ts path: {relative!r}")
        text, count = rewrite_base_url(code, base_url)
        rewrites += count
        target = workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    (workspace / "playwright.config.ts").write_text(
        CONFIG.format(base_url=json.dumps(base_url.rstrip("/"))), encoding="utf-8")
    return rewrites


def playwright_binary(samples_root: Path) -> Path:
    binary = samples_root / "node_modules" / ".bin" / "playwright"
    if not binary.exists():
        raise RuntimeError(f"{binary} missing — run `npm install` inside samples/ first")
    return binary


def parse_report(report: dict, specs: list[str]) -> list[dict]:
    """Flatten Playwright's nested JSON report into one row per test.

    A describe block is a nested suite, so the specs that matter are below the
    file entry rather than at the top level. Paths in the report are workspace
    relative in some versions and absolute in others; match on the file name,
    which is unique across a suite.
    """
    by_name = {PurePosixPath(spec).name: spec for spec in specs}
    tests: list[dict] = []

    def walk(suite: dict) -> None:
        for spec in suite.get("specs", []):
            result = spec["tests"][0]["results"][0]
            tests.append({"spec": by_name.get(PurePosixPath(spec["file"]).name, spec["file"]),
                          "name": spec["title"], "status": result["status"],
                          "duration_ms": round(result.get("duration") or 0),
                          "error": plain((result.get("errors") or [{}])[0].get("message", ""))[:400]})
        for child in suite.get("suites", []):
            walk(child)

    for suite in report.get("suites", []):
        walk(suite)
    return tests


def run_specs(samples_root: Path, tree: dict[str, str], specs: list[str], base_url: str,
              *, keep: Path | None = None, timeout: float = 900.0) -> dict:
    """Run named spec files of one materialized tree and return parsed results.

    The workspace symlinks samples/node_modules so Node resolves @playwright/test
    without a second install, and so the browser binaries already downloaded for
    the samples are the ones used here.
    """
    binary = playwright_binary(samples_root)
    workspace = keep or Path(mkdtemp(prefix="s2p-exec-"))
    workspace.mkdir(parents=True, exist_ok=True)
    started = perf_counter()
    try:
        (workspace / "node_modules").unlink(missing_ok=True)
        (workspace / "node_modules").symlink_to(samples_root / "node_modules")
        rewrites = _write_tree(workspace, tree, base_url)
        process = subprocess.run(
            [str(binary), "test", "--workers", "1", "--retries", "0", "--reporter", "json", *specs],
            cwd=workspace, capture_output=True, text=True, timeout=timeout,
            env=os.environ | {"CI": "1"},
        )
        try:
            report = json.loads(process.stdout)
        except json.JSONDecodeError:
            return {"ran": False, "exit_code": process.returncode, "tests": [], "rewrites": rewrites,
                    "errors": [plain(process.stderr or process.stdout)[:2000] or "no JSON report"],
                    "elapsed_seconds": perf_counter() - started}
        return {"ran": True, "exit_code": process.returncode, "rewrites": rewrites,
                "tests": parse_report(report, specs),
                "errors": [plain(error.get("message", ""))[:2000] for error in report.get("errors", [])],
                "elapsed_seconds": perf_counter() - started}
    except subprocess.TimeoutExpired:
        return {"ran": False, "exit_code": None, "tests": [], "rewrites": 0,
                "errors": [f"playwright test exceeded {timeout:.0f}s"],
                "elapsed_seconds": perf_counter() - started}
    finally:
        if keep is None:
            shutil.rmtree(workspace, ignore_errors=True)


def divergence(suite_name: str, spec: str, title: str) -> dict | None:
    """The declared reason this exact test cannot pass against the pinned app, if any."""
    return KNOWN_APP_DIVERGENCES.get((suite_name, spec, title))


def score(suite_name: str, expected: list[str], run: dict) -> dict:
    """Turn one browser run into a row outcome: passed, failed, or error.

    Missing test names are a failure of their own. A converted test that quietly
    drops a case would otherwise report "everything that ran, passed".
    """
    ran = {test["name"]: test for test in run["tests"]}
    diverged = [{**test, **divergence(suite_name, test["spec"], test["name"])}
                for test in run["tests"] if divergence(suite_name, test["spec"], test["name"])]
    diverged_names = {test["name"] for test in diverged}
    failed = [test for test in run["tests"]
              if test["status"] != "passed" and test["name"] not in diverged_names]
    missing = [name for name in expected if name not in ran]
    unexpected = [name for name in ran if name not in expected]
    if not run["ran"] or run["errors"]:
        status = "error"
    elif failed or missing:
        status = "failed"
    else:
        status = "passed"
    return {"status": status, "expected": expected, "tests": run["tests"], "failed": failed,
            "missing": missing, "unexpected": unexpected, "divergences": diverged,
            "errors": run["errors"], "url_rewrites": run["rewrites"],
            "elapsed_seconds": round(run["elapsed_seconds"], 2)}


def execute_case(root: Path, suite: Suite, case_id: str, code: str | None, base_url: str,
                 *, keep: Path | None = None) -> dict:
    """Execute one dataset row: the golden tree with this one file replaced by `code`.

    Passing code=None runs the row's spec against the untouched golden tree,
    which is how the CI gate measures the fixtures themselves.
    """
    evidence = browser_evidence(root, suite, case_id)
    tree = golden_tree(root / "samples", suite)
    path = suite.path_of(case_id)
    golden = tree[path]
    substituted = code is not None
    if substituted:
        tree[path] = code
    run = run_specs(root / "samples", tree, [evidence["spec"]], base_url, keep=keep)
    return {"case_id": case_id, "suite": suite.name, "path": path, "spec": evidence["spec"],
            "substituted": substituted, "code_sha256": sha256_text(tree[path]),
            "golden_sha256": sha256_text(golden),
            **score(suite.name, evidence["expected"], run)}
