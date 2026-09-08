"""Step 11.2 — the execution eval: harness contracts, and the line it must not cross.

Nothing here starts a browser or a container. The browser numbers live in
docs/phase-11.2-report.md; these tests protect the rules around them.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from selenium2playwright import eval_execution as scorecard
from selenium2playwright import execution as ex

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"

# Every module the deployed service or the CLI can reach. Executing code is an
# evaluation activity; production validation stays static (plan.md safety rule).
PRODUCTION_MODULES = ("graph.py", "cli.py", "http_app.py", "server.py", "playground.py",
                      "suite.py", "suite_graph.py", "assemble.py", "eval_target.py")


def run_of(*tests, errors=(), rewrites=0, ran=True):
    return {"ran": ran, "exit_code": 0, "rewrites": rewrites, "elapsed_seconds": 1.0,
            "errors": list(errors), "tests": list(tests)}


def case_of(spec, name, status="passed", error=""):
    # Not `test_of`: pytest collects any module-level `test_*` as a test,
    # then reads its parameters as fixture names and errors at setup. It
    # was one red line in every full run that meant nothing.
    return {"spec": spec, "name": name, "status": status, "duration_ms": 5, "error": error}


class SafetyBoundaryTests(unittest.TestCase):
    def test_no_production_module_imports_the_executor(self):
        """The graph must never be able to run the code it just wrote."""
        forbidden = ("from selenium2playwright.execution", "from selenium2playwright.eval_execution",
                     "from selenium2playwright import execution", "import selenium2playwright.execution")
        for name in PRODUCTION_MODULES:
            source = (ROOT / "src/selenium2playwright" / name).read_text(encoding="utf-8")
            for statement in forbidden:
                self.assertNotIn(statement, source, f"{name} reaches the execution harness")

    def test_the_validators_package_stays_static(self):
        for path in (ROOT / "src/selenium2playwright/validators").glob("*.py"):
            self.assertNotIn("from selenium2playwright.execution", path.read_text(encoding="utf-8"))


class UrlRewriteTests(unittest.TestCase):
    def test_hard_coded_production_urls_are_pointed_at_the_app_under_test(self):
        code = 'await page.goto("https://the-internet.herokuapp.com/login");'
        text, count = ex.rewrite_base_url(code, "http://127.0.0.1:7080")
        self.assertEqual(count, 1)
        self.assertIn('"http://127.0.0.1:7080/login"', text)

    def test_running_against_production_rewrites_nothing(self):
        code = 'await page.goto("https://the-internet.herokuapp.com/login");'
        self.assertEqual(ex.rewrite_base_url(code, ex.PUBLIC_BASE_URL), (code, 0))

    def test_relative_navigation_is_untouched(self):
        code = 'await page.goto("/login");'
        self.assertEqual(ex.rewrite_base_url(code, ex.LOCAL_BASE_URL), (code, 0))


class WorkspaceTests(unittest.TestCase):
    def test_a_path_that_escapes_the_workspace_is_refused(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                ex._write_tree(Path(folder), {"../outside.ts": "export const x = 1;\n"}, ex.LOCAL_BASE_URL)

    def test_the_generated_config_pins_the_base_url_it_was_given(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            ex._write_tree(Path(folder), {"tests/a.spec.ts": "export const x = 1;\n"}, "http://127.0.0.1:7080")
            config = (Path(folder) / "playwright.config.ts").read_text(encoding="utf-8")
            self.assertIn('baseURL: "http://127.0.0.1:7080"', config)


class ReportParsingTests(unittest.TestCase):
    def test_specs_inside_a_describe_block_are_found(self):
        report = {"suites": [{"specs": [], "suites": [{"specs": [
            {"file": "/abs/tmp/tests/login.spec.ts", "title": "logs in",
             "tests": [{"results": [{"status": "passed", "duration": 12.4, "errors": []}]}]}]}]}]}
        tests = ex.parse_report(report, ["tests/login.spec.ts"])
        self.assertEqual(tests, [{"spec": "tests/login.spec.ts", "name": "logs in",
                                  "status": "passed", "duration_ms": 12, "error": ""}])

    def test_terminal_colour_codes_do_not_reach_the_report(self):
        report = {"suites": [{"specs": [{"file": "tests/a.spec.ts", "title": "t", "tests": [
            {"results": [{"status": "failed", "duration": 1,
                          "errors": [{"message": "\x1b[31mExpected\x1b[39m x"}]}]}]}]}]}
        self.assertEqual(ex.parse_report(report, ["tests/a.spec.ts"])[0]["error"], "Expected x")


class ScoringTests(unittest.TestCase):
    def test_a_test_that_never_ran_fails_the_row(self):
        """A converted file that quietly drops a case must not report 'all green'."""
        result = ex.score("hard", ["reads the middle frame", "reads the bottom frame"],
                          run_of(case_of("tests/nested-frames.spec.ts", "reads the middle frame")))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["missing"], ["reads the bottom frame"])

    def test_a_declared_app_divergence_does_not_fail_the_row(self):
        result = ex.score("base", ["accepts a JavaScript alert"],
                          run_of(case_of("tests/alerts.spec.ts", "accepts a JavaScript alert", "failed")))
        self.assertEqual(result["status"], "passed")
        self.assertEqual(len(result["divergences"]), 1)
        self.assertEqual(result["failed"], [])

    def test_an_undeclared_failure_still_fails(self):
        result = ex.score("base", ["dismisses a JavaScript confirmation"],
                          run_of(case_of("tests/alerts.spec.ts", "dismisses a JavaScript confirmation", "failed")))
        self.assertEqual(result["status"], "failed")

    def test_a_suite_that_could_not_run_is_an_error_not_a_failure(self):
        result = ex.score("hard", ["x"], run_of(ran=False, errors=["no JSON report"]))
        self.assertEqual(result["status"], "error")

    def test_an_extra_test_is_reported_but_not_fatal(self):
        result = ex.score("hard", ["a"], run_of(case_of("tests/a.spec.ts", "a"), case_of("tests/a.spec.ts", "b")))
        self.assertEqual((result["status"], result["unexpected"]), ("passed", ["b"]))


class DivergenceTests(unittest.TestCase):
    def test_every_declared_divergence_names_a_real_test_of_a_real_suite(self):
        """A stale entry would silently excuse a test that no longer exists."""
        for (suite_name, spec, title), note in ex.KNOWN_APP_DIVERGENCES.items():
            suite = ex.suite_for(suite_name)
            self.assertIn(spec, ex.golden_tree(SAMPLES, suite), f"{spec} is not in {suite_name}")
            evidence = json.loads((ROOT / suite.evidence).read_text(encoding="utf-8"))
            names = {test["name"] for case in evidence["cases"].values()
                     for test in case["reference_browser"]}
            self.assertIn(title, names, f"{title} was never measured in {suite_name}")
            self.assertTrue(note["why"] and note["pinned_app"] != note["production_app"])

    def test_a_divergence_that_starts_passing_fails_the_gate(self):
        expected = {"tests/alerts.spec.ts": ["accepts a JavaScript alert"]}
        _, failures, _ = scorecard.verdicts(
            "base", expected, run_of(case_of("tests/alerts.spec.ts", "accepts a JavaScript alert")))
        self.assertEqual(len(failures), 1)
        self.assertIn("stale", failures[0])

    def test_a_divergence_that_keeps_failing_is_accepted(self):
        expected = {"tests/alerts.spec.ts": ["accepts a JavaScript alert"]}
        checks, failures, _ = scorecard.verdicts(
            "base", expected, run_of(case_of("tests/alerts.spec.ts", "accepts a JavaScript alert", "failed")))
        self.assertEqual(failures, [])
        self.assertEqual(checks[0]["verdict"], "divergent")

    def test_a_missing_test_fails_the_gate(self):
        _, failures, _ = scorecard.verdicts("hard", {"tests/hovers.spec.ts": ["reveals it"]}, run_of())
        self.assertEqual(len(failures), 1)
        self.assertIn("missing", failures[0])


class EvidenceTests(unittest.TestCase):
    def test_a_page_object_borrows_the_spec_that_exercises_it(self):
        evidence = ex.browser_evidence(ROOT, ex.SUITES["hard"], "hovers-page")
        self.assertEqual(evidence["spec"], "tests/hovers.spec.ts")
        self.assertEqual(evidence["test_case_id"], "hovers-test")
        self.assertTrue(evidence["expected"])

    def test_a_test_row_owns_its_own_evidence(self):
        evidence = ex.browser_evidence(ROOT, ex.SUITES["base"], "alerts-test")
        self.assertEqual((evidence["spec"], evidence["test_case_id"]),
                         ("tests/alerts.spec.ts", "alerts-test"))
        self.assertEqual(len(evidence["expected"]), 2)

    def test_every_case_of_both_suites_resolves_to_a_runnable_spec(self):
        for name, suite in ex.SUITES.items():
            tree = ex.golden_tree(SAMPLES, suite)
            for case in suite.cases:
                evidence = ex.browser_evidence(ROOT, suite, case.case_id)
                self.assertIn(evidence["spec"], tree, f"{name}/{case.case_id}")
                self.assertIn(suite.path_of(case.case_id), tree)

    def test_an_unknown_suite_is_refused(self):
        with self.assertRaises(ValueError):
            ex.suite_for("nonexistent")


class ScorecardTests(unittest.TestCase):
    def test_a_saved_experiment_is_read_from_its_assembled_report(self):
        import tempfile
        report = {"plan": {"metadata": {"benchmark": "hard"}, "dataset_name": "x"},
                  "rows": [{"case_id": "hovers-page", "kind": "page-object", "hard_cases": [8],
                            "outputs": {"code": "x", "conversion_status": "passed"},
                            "metrics": {key: {"status": "passed"} for key in scorecard.STATIC_KEYS}}]}
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "report.json").write_text(json.dumps(report), encoding="utf-8")
            benchmark, rows = scorecard.saved_rows(Path(folder))
        self.assertEqual(benchmark, "hard")
        self.assertTrue(scorecard.all_static_passed(rows[0]))

    def test_a_plan_written_before_the_benchmark_key_is_read_as_the_base_set(self):
        self.assertEqual(ex.benchmark_of({"dataset_name": "selenium2playwright-v1-4920b5f319d8",
                                          "metadata": {}}), "base")
        self.assertEqual(ex.benchmark_of({"dataset_name": "selenium2playwright-hard-v1-b233d4",
                                          "metadata": {}}), "hard")

    def test_rows_without_code_never_count_as_executed(self):
        totals = scorecard.summarize([{"status": "passed"}, {"status": "not_run"}, {"status": "failed"}])
        self.assertEqual((totals["scheduled"], totals["executed"], totals["passed"]), (3, 2, 1))
        self.assertEqual(totals["pass_percent_of_scheduled"], 33.33)
        self.assertEqual(totals["pass_percent_of_executed"], 50.0)

    def test_the_quadrant_that_motivates_this_step_is_counted(self):
        results = [{"case_id": "a", "status": "failed"}, {"case_id": "b", "status": "passed"},
                   {"case_id": "c", "status": "not_run"}]
        rows = {"a": {"static": {key: "passed" for key in scorecard.STATIC_KEYS}},
                "b": {"static": {key: "failed" for key in scorecard.STATIC_KEYS}},
                "c": {"static": {key: "passed" for key in scorecard.STATIC_KEYS}}}
        buckets = scorecard.quadrants(results, rows)
        self.assertEqual(buckets["static_passed_execution_failed"], ["a"])
        self.assertEqual(buckets["static_failed_execution_passed"], ["b"])
        self.assertEqual(buckets["both_passed"], [])


class AppTests(unittest.TestCase):
    def test_an_app_that_is_not_there_is_reported_not_raised(self):
        status = ex.app_status("http://127.0.0.1:1", timeout=1.0)
        self.assertFalse(status["reachable"])
        self.assertIn("error", status)

    def test_the_image_is_pinned_by_digest_not_by_a_movable_tag(self):
        self.assertIn("@sha256:", ex.APP_IMAGE)
        compose = (ROOT / "deploy/the-internet/compose.yml").read_text(encoding="utf-8")
        self.assertIn(ex.APP_IMAGE, compose)


if __name__ == "__main__":
    unittest.main()
