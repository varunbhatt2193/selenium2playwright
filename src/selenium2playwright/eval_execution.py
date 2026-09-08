"""Step 11.2 — the execution scorecard: replay saved conversions through a browser.

The converter is never called here. A finished experiment already contains the
generated code for every row, so measuring "does it actually run" over the
Phase 6.2 baseline or the 11.1b arms costs browser time and nothing else — no
tokens, no provider account, no second sample of a non-deterministic model.

Two modes, one shape of report:

* ``execute_goldens`` runs the untouched fixtures. That is the CI gate: it
  proves the ruler still measures, and it is what a pull request runs.
* ``execute_experiment`` puts each row's converted file back into the golden
  tree and runs only the browser test that exercises it. That is the number —
  execution pass %, the fifth column beside compile/residue/lint/parity.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from selenium2playwright.execution import (APP_IMAGE, APP_VERSION, KNOWN_APP_DIVERGENCES,
                                           browser_evidence, divergence, execute_case,
                                           golden_tree, run_specs, sha256_text, suite_for)

STATIC_KEYS = ("compiles", "residue_free", "typed_lint_pass", "parity_pass")


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def saved_rows(folder: Path) -> tuple[str, list[dict]]:
    """Read one finished experiment: which benchmark it ran, and each row's code.

    report.json is the assembled evidence, so this reads the same rows the
    published scorecard did — not a re-derivation from the raw journal.
    """
    report = json.loads((folder / "report.json").read_text(encoding="utf-8"))
    plan = report["plan"]
    benchmark = plan["metadata"].get("benchmark") or (
        "hard" if "-hard-" in plan.get("dataset_name", "") else "base")
    rows = []
    for row in report["rows"]:
        outputs = row.get("outputs") or {}
        rows.append({"case_id": row["case_id"], "kind": row.get("kind", ""),
                     "code": outputs.get("code"),
                     "conversion_status": outputs.get("conversion_status"),
                     "hard_cases": row.get("hard_cases", []),
                     "static": {key: row["metrics"].get(key, {}).get("status") for key in STATIC_KEYS}})
    return benchmark, rows


def all_static_passed(row: dict) -> bool:
    return all(row["static"].get(key) == "passed" for key in STATIC_KEYS)


def summarize(results: list[dict]) -> dict:
    """Counts first, percentage second; the denominator is every scheduled row."""
    statuses = {"passed": 0, "failed": 0, "error": 0, "not_run": 0}
    for result in results:
        statuses[result["status"]] = statuses.get(result["status"], 0) + 1
    scheduled = len(results)
    executed = scheduled - statuses["not_run"]
    return {"scheduled": scheduled, "executed": executed, "passed": statuses["passed"],
            "statuses": statuses,
            "pass_percent_of_scheduled": round(100 * statuses["passed"] / scheduled, 2) if scheduled else None,
            "pass_percent_of_executed": round(100 * statuses["passed"] / executed, 2) if executed else None}


def quadrants(results: list[dict], rows: dict[str, dict]) -> dict:
    """Where the browser and the four gates disagree — the reason this step exists.

    static_passed_execution_failed is the interesting one: code that compiles,
    lints, keeps its assertions and carries no Selenium residue, and still does
    not do the job. No static gate can see that row.
    """
    buckets = {"both_passed": [], "static_passed_execution_failed": [],
               "static_failed_execution_passed": [], "both_failed": []}
    for result in results:
        row = rows.get(result["case_id"], {})
        if result["status"] == "not_run":
            continue
        static, ran = all_static_passed(row), result["status"] == "passed"
        key = ("both_passed" if static and ran else
               "static_passed_execution_failed" if static else
               "static_failed_execution_passed" if ran else "both_failed")
        buckets[key].append(result["case_id"])
    return buckets


def execute_experiment(root: Path, folder: Path, base_url: str, *, only: list[str] | None = None,
                       progress=print) -> dict:
    """Run every saved conversion of one experiment and score it."""
    benchmark, rows = saved_rows(folder)
    suite = suite_for(benchmark)
    by_case = {row["case_id"]: row for row in rows}
    results = []
    for row in rows:
        if only and row["case_id"] not in only:
            continue
        if not row["code"]:
            results.append({"case_id": row["case_id"], "suite": suite.name, "status": "not_run",
                            "reason": f"conversion produced no code ({row['conversion_status']})",
                            "path": suite.path_of(row["case_id"])})
        else:
            results.append(execute_case(root, suite, row["case_id"], row["code"], base_url))
        results[-1]["kind"] = row["kind"]
        results[-1]["static"] = row["static"]
        results[-1]["all_static_passed"] = all_static_passed(row)
        results[-1]["hard_cases"] = row["hard_cases"]
        progress(f"  {len(results)}/{len(rows)} {row['case_id']}: {results[-1]['status']}")
    return {"schema_version": 1, "mode": "experiment", "suite": suite.name,
            "experiment_dir": str(folder), "generated_at_utc": stamp(),
            "app": {"base_url": base_url, "image": APP_IMAGE, "version": APP_VERSION},
            "aggregate": summarize(results),
            # Test rows are handed golden page objects, so their interface is
            # supplied and execution measures behaviour. Page-object rows are run
            # against the golden caller, which names members the converter was
            # never shown — so read the two groups separately, never as one number.
            "by_kind": {kind: summarize([r for r in results if r["kind"] == kind])
                        for kind in sorted({r["kind"] for r in results})},
            "quadrants": quadrants(results, by_case), "results": results}


def verdicts(suite_name: str, expected: dict[str, list[str]], run: dict) -> tuple[list, list, list]:
    """Judge one whole-suite run against the names that were measured.

    Pure: no browser, no disk. A declared divergence that FAILS is expected and
    excluded; a declared divergence that PASSES is a failure of its own, because
    it means the app moved and the list no longer describes it.
    """
    observed = {(test["spec"], test["name"]): test for test in run["tests"]}
    checks, failures = [], []
    for spec, names in expected.items():
        for name in names:
            test = observed.get((spec, name))
            declared = divergence(suite_name, spec, name)
            status = test["status"] if test else "missing"
            verdict = ("divergent" if declared and status != "passed" else
                       "stale_divergence" if declared else
                       "passed" if status == "passed" else "failed")
            checks.append({"spec": spec, "name": name, "status": status, "verdict": verdict,
                           "duration_ms": test["duration_ms"] if test else None,
                           "error": test["error"] if test else "",
                           "divergence": declared})
            if verdict == "failed":
                first = test["error"].splitlines()[0] if test and test["error"] else ""
                failures.append(f"{spec} :: {name} -> {status}" + (f" — {first}" if first else ""))
            if verdict == "stale_divergence":
                failures.append(f"{spec} :: {name} now PASSES against the pinned app; "
                                "the declared divergence is stale")
    failures += [f"suite error: {error}" for error in run["errors"]]
    unexpected = [f"{spec} :: {name}" for (spec, name) in observed
                  if name not in expected.get(spec, [])]
    return checks, failures, unexpected


def execute_suite(root: Path, benchmark: str, tree: dict[str, str], base_url: str,
                  *, mode: str, source: str) -> dict:
    """Run a whole suite tree at once and check it against the measured evidence.

    The golden tree run this way is the CI gate. A converted tree run this way is
    the whole-suite execution eval: unlike a single-file row, its page objects and
    its tests were converted together, so the interface between them is the
    converter's own and nobody's names had to be guessed.
    """
    suite = suite_for(benchmark)
    evidence = {case.case_id: browser_evidence(root, suite, case.case_id) for case in suite.cases}
    specs = sorted({item["spec"] for item in evidence.values()})
    expected = {spec: [] for spec in specs}
    for case in suite.cases:
        item = evidence[case.case_id]
        if item["test_case_id"] == case.case_id:
            expected[item["spec"]] = item["expected"]
    missing_files = sorted(set(golden_tree(root / "samples", suite)) - set(tree))
    run = run_specs(root / "samples", tree, [spec for spec in specs if spec in tree], base_url)
    checks, failures, unexpected = verdicts(suite.name, expected, run)
    failures += [f"file missing from the tree: {path}" for path in missing_files]
    counts = {verdict: sum(1 for check in checks if check["verdict"] == verdict)
              for verdict in ("passed", "failed", "divergent", "stale_divergence")}
    return {"schema_version": 1, "mode": mode, "suite": suite.name, "source": source,
            "generated_at_utc": stamp(),
            "app": {"base_url": base_url, "image": APP_IMAGE, "version": APP_VERSION},
            "tree_sha256": {path: sha256_text(code) for path, code in sorted(tree.items())},
            "missing_files": missing_files, "url_rewrites": run["rewrites"],
            "declared_divergences": len([key for key in KNOWN_APP_DIVERGENCES if key[0] == suite.name]),
            "counts": counts, "checks": checks, "unexpected_tests": unexpected,
            "elapsed_seconds": round(run["elapsed_seconds"], 2), "passed": not failures,
            "failures": failures}


def execute_goldens(root: Path, benchmark: str, base_url: str) -> dict:
    """Run the untouched fixtures: the gate a pull request runs.

    It answers one question — does the reference suite, the thing every converter
    score is measured against, still do what it did when it was measured?
    """
    suite = suite_for(benchmark)
    return execute_suite(root, benchmark, golden_tree(root / "samples", suite), base_url,
                         mode="goldens", source=f"samples/{suite.golden_dir}")


def read_tree(folder: Path) -> dict[str, str]:
    """Every .ts file of a converted tree, keyed by the path its imports assume."""
    return {path.relative_to(folder).as_posix(): path.read_text(encoding="utf-8")
            for path in sorted(folder.rglob("*.ts"))}


def execute_tree(root: Path, benchmark: str, folder: Path, base_url: str) -> dict:
    """Run a whole converted tree — the suite-mode counterpart of a single-file row."""
    return execute_suite(root, benchmark, read_tree(folder), base_url,
                         mode="tree", source=str(folder))


def render_goldens_markdown(report: dict) -> str:
    """The gate's own receipt: what ran, what diverged, and why the exit code is what it is."""
    app = report["app"]
    lines = [f"# Execution {'gate' if report['mode'] == 'goldens' else 'eval'} — "
             f"{report['suite']} {'fixtures' if report['mode'] == 'goldens' else 'tree'}", "",
             f"Result: **{'passed' if report['passed'] else 'FAILED'}** "
             f"({report['elapsed_seconds']}s, {report['generated_at_utc']}).",
             f"App: `{app['image']}` (the-internet {app['version']}) at {app['base_url']}.",
             f"Tree under test: `{report['source']}` ({len(report['tree_sha256'])} files).", "",
             ("This runs the untouched golden fixtures. It scores no conversion: it proves the ruler still "
              "measures."
              if report["mode"] == "goldens" else
              "This runs a whole converted tree. Its page objects and its tests were converted together, so "
              "the interface between them is the converter's own."),
             "Expected test names come from the fixture evidence measured in Step 6.1 / 11.1a, so a suite",
             "that quietly stops running a test fails here rather than passing.", ""]
    if report["failures"]:
        lines += ["## Failures", ""] + [f"- {failure}" for failure in report["failures"]] + [""]
    counts = report["counts"]
    lines += [f"Passed {counts['passed']}, failed {counts['failed']}, "
              f"declared divergences {counts['divergent']} of {report['declared_divergences']}, "
              f"stale divergences {counts['stale_divergence']}.", "",
              "| Spec | Test | Status | Verdict |", "| --- | --- | --- | --- |"]
    for check in report["checks"]:
        lines.append(f"| `{check['spec']}` | {check['name']} | {check['status']} | {check['verdict']} |")
    diverged = [check for check in report["checks"] if check["divergence"]]
    if diverged:
        lines += ["", "## Declared app divergences", "",
                  "The pinned image is a 2020 build; production has moved since. Each entry was measured",
                  "by running the same untouched golden against both, never assumed. A divergent test is",
                  "excluded from pass/fail — and if one starts passing, this gate fails, because that",
                  "means the app changed and the list is stale.", ""]
        for check in diverged:
            note = check["divergence"]
            lines += [f"- **{check['spec']} :: {check['name']}** — pinned app says "
                      f"`{note['pinned_app']}`, production says `{note['production_app']}`. {note['why']}"]
    if report["unexpected_tests"]:
        lines += ["", "## Tests that ran but were not in the measured evidence", ""]
        lines += [f"- {name}" for name in report["unexpected_tests"]]
    return "\n".join(lines) + "\n"


def render_experiment_markdown(report: dict) -> str:
    """Failures first, then the number, then every row."""
    totals, app = report["aggregate"], report["app"]
    lines = [f"# Execution eval — {report['suite']} benchmark", "",
             f"Source experiment: `{report['experiment_dir']}` (no model was called; this replays",
             "the code that experiment already produced).",
             f"App: `{app['image']}` (the-internet {app['version']}) at {app['base_url']}.",
             f"Measured {report['generated_at_utc']}.", "",
             "Each row is the golden tree with **one** file replaced by the converted one, running only",
             "the browser test that exercises it. A red row therefore names a file, not a suite.", ""]
    failed = [result for result in report["results"] if result["status"] != "passed"]
    if failed:
        lines += ["## Rows that did not run green", ""]
        for result in failed:
            detail = result.get("reason") or "; ".join(
                [f"{test['name']}: {test['status']}" for test in result.get("failed", [])]
                + [f"missing: {name}" for name in result.get("missing", [])]
                + result.get("errors", []))
            lines.append(f"- **{result['case_id']}** ({result['status']}) — {detail[:300]}")
        lines.append("")
    lines += [f"Execution passed: **{totals['passed']} / {totals['scheduled']}** "
              f"({totals['pass_percent_of_scheduled']}% of scheduled; "
              f"{totals['pass_percent_of_executed']}% of the {totals['executed']} that produced code).",
              f"Statuses: {totals['statuses']}.", "",
              "| Row kind | Passed / scheduled | Percent |", "| --- | --- | --- |"]
    for kind, group in report.get("by_kind", {}).items():
        lines.append(f"| {kind} | {group['passed']} / {group['scheduled']} | "
                     f"{group['pass_percent_of_scheduled']}% |")
    lines += ["",
              "A **test** row is handed golden page objects, so its interface is supplied and this",
              "measures behaviour. A **page-object** row is run against the golden caller, which names",
              "members the converter was never shown — a red one can be a defect or an unguessable name,",
              "and only reading the file says which. Both readings are in the report for this run.", "",
              "## Where the browser and the four gates disagree", "",
              "The middle row is why this step exists: code that compiles, lints, keeps every assertion",
              "and contains no Selenium residue, and still does not do the job.", "",
              "| Quadrant | Rows | Cases |", "| --- | --- | --- |"]
    labels = {"both_passed": "static passed · execution passed",
              "static_passed_execution_failed": "static passed · **execution failed**",
              "static_failed_execution_passed": "static failed · execution passed",
              "both_failed": "static failed · execution failed"}
    for key, label in labels.items():
        cases = report["quadrants"][key]
        lines.append(f"| {label} | {len(cases)} | {', '.join(cases) or '—'} |")
    lines += ["", "## Every row", "",
              "| Case | File | Spec | Static gates | Execution | Tests | Notes |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
    for result in report["results"]:
        ran = result.get("tests", [])
        detail = ", ".join(sorted({test["status"] for test in ran})) or "—"
        notes = []
        if result.get("missing"):
            notes.append(f"missing: {', '.join(result['missing'])}")
        if result.get("unexpected"):
            notes.append(f"extra: {', '.join(result['unexpected'])}")
        if result.get("divergences"):
            notes.append(f"{len(result['divergences'])} declared app divergence(s) excluded")
        if result.get("url_rewrites"):
            notes.append(f"{result['url_rewrites']} hard-coded URL(s) pointed at the local app")
        if result.get("reason"):
            notes.append(result["reason"])
        lines.append(f"| {result['case_id']} | `{result['path']}` | `{result.get('spec', '—')}` | "
                     f"{'all passed' if result.get('all_static_passed') else 'not all passed'} | "
                     f"{result['status']} | {len(ran)} ({detail}) | {'; '.join(notes) or '—'} |")
    return "\n".join(lines) + "\n"
