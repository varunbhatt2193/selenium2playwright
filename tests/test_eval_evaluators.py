"""Known-good/bad candidates and SDK feedback checks; no model or cloud calls."""

import copy
import json
import os
import subprocess
import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from langsmith import tracing_context
from langsmith.evaluation import EvaluationResult, run_evaluator
from langsmith.schemas import Example, Run

from selenium2playwright import eval_evaluators as evaluators
from selenium2playwright.eval_collection import build_collection
from selenium2playwright.schemas import Finding, ValidationReport

ROOT = Path(__file__).resolve().parents[1]


class EvaluatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        collection = build_collection(ROOT / "samples", ROOT / "docs/evaluation-fixture-evidence.json")
        cls.rows = {row["metadata"]["case_id"]: row for row in collection["examples"]}

    def setUp(self):
        stack = self.enterContext(ExitStack())
        stack.enter_context(patch.dict(os.environ, {
            "LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false"}))
        stack.enter_context(tracing_context(enabled=False))

    def test_golden_pom_and_test_pass_with_json_evidence_and_unchanged_inputs(self):
        for case in ("login-page", "login-test"):
            row = copy.deepcopy(self.rows[case])
            original = copy.deepcopy(row)
            for evaluator in evaluators.EVALUATORS:
                with self.subTest(case=case, evaluator=evaluator.__name__):
                    score, status = evaluator(row["inputs"], row["outputs"])
                    self.assertEqual(score["score"], 1, score["comment"])
                    self.assertEqual(status["value"], "passed")
                    self.assertEqual(status["key"], score["key"] + "_status")
                    self.assertIsNone(score["evaluator_info"]["error"])
                    self.assertGreaterEqual(score["evaluator_info"]["elapsed_seconds"], 0)
                    self.assertEqual(json.loads(json.dumps(score)), score)
                    EvaluationResult.model_validate(score)
            self.assertEqual(row, original)

    def test_compiler_checks_actual_code_despite_a_copied_success_report(self):
        row = self.rows["login-page"]
        code = row["outputs"]["code"].replace(".fill(", ".fil(", 1)
        self.assertNotEqual(code, row["outputs"]["code"])
        output = {"code": code, "report": {"status": "passed", "validation": [
            {"gate": "compile", "passed": True}]}}
        score, status = evaluators.compiles(row["inputs"], output)
        self.assertEqual((score["score"], status["value"]), (0, "failed"))
        self.assertIn("TS2551", score["comment"])
        self.assertIn("fil", score["evaluator_info"]["report"]["tool_output"])

    def test_missing_await_passes_compile_but_fails_typed_lint(self):
        row = self.rows["login-page"]
        code = row["outputs"]["code"].replace("await this.usernameInput.fill", "this.usernameInput.fill")
        self.assertNotEqual(code, row["outputs"]["code"])
        output = {"code": code}
        self.assertEqual(evaluators.compiles(row["inputs"], output)[0]["score"], 1)
        score, status = evaluators.typed_lint_pass(row["inputs"], output)
        self.assertEqual((score["score"], status["value"]), (0, "failed"))
        self.assertIn("no-floating-promises", score["comment"])

    def test_residue_and_removed_assertion_fail_their_respective_checks(self):
        row = self.rows["login-page"]
        score, status = evaluators.residue_free(row["inputs"], {"code": row["inputs"]["source"]})
        self.assertEqual((score["score"], status["value"]), (0, "failed"))
        self.assertIn("forbidden-import", score["comment"])
        row = self.rows["login-test"]
        code = row["outputs"]["code"].replace(
            '    await expect(loginPage.flashMessage).toContainText(\n'
            '      "Your password is invalid!"\n    );', "")
        self.assertNotEqual(code, row["outputs"]["code"])
        score, status = evaluators.parity_pass(row["inputs"], {"code": code})
        self.assertEqual((score["score"], status["value"]), (0, "failed"))
        self.assertIn("missing-assertion", score["comment"])
        self.assertIn("rejects invalid credentials", score["comment"])

    def test_missing_or_broken_companion_is_a_compile_failure(self):
        row = self.rows["login-test"]
        companion = row["inputs"]["context_files"]["pages/LoginPage.ts"]
        for companions, diagnostic in (({}, "TS2307"), ({"pages/LoginPage.ts": companion.replace(
                ".fill(", ".fil(", 1)}, "TS2551")):
            with self.subTest(diagnostic=diagnostic):
                inputs = row["inputs"] | {"context_files": companions}
                score, status = evaluators.compiles(inputs, row["outputs"])
                self.assertEqual((score["score"], status["value"]), (0, "failed"))
                self.assertIn(diagnostic, score["comment"])

    def test_cancel_to_ok_bug_still_passes_static_evaluation(self):
        # Preserve this blind spot explicitly; the browser caught it in 6.1.
        # This test deliberately does not claim to execute browser behavior.
        row = self.rows["alerts-page"]
        code = row["outputs"]["code"].replace("dialog.dismiss()", "dialog.accept()")
        self.assertNotEqual(code, row["outputs"]["code"])
        for evaluator in evaluators.EVALUATORS:
            with self.subTest(evaluator=evaluator.__name__):
                score, status = evaluator(row["inputs"], {"code": code})
                self.assertEqual((score["score"], status["value"]), (1, "passed"), score["comment"])

    def test_missing_output_and_invalid_paths_never_invoke_a_validator(self):
        row = self.rows["login-page"]
        with ExitStack() as stack:
            checks = [stack.enter_context(patch.object(evaluators, gate + "_check"))
                      for gate in evaluators.GATE_KEYS]
            for evaluator in evaluators.EVALUATORS:
                for output in (None, {}, {"code": None}, {"code": " "}, {"code": 42}):
                    with self.subTest(evaluator=evaluator.__name__, output=output):
                        score, status = evaluator(row["inputs"], output)
                        self.assertEqual((score["score"], status["value"]), (0, "no_output"))
                        self.assertIsNone(score["evaluator_info"]["report"])
                for inputs in (row, row["inputs"] | {"source_path": "../escape.ts"},
                               row["inputs"] | {"context_files": {"/tmp/escape.ts": "export {};"}}):
                    score, status = evaluator(inputs, row["outputs"])
                    self.assertEqual((score["score"], status["value"]), (0, "invalid_input"))
            for check in checks:
                check.assert_not_called()

    def test_tool_exceptions_are_zero_with_error_evidence_and_other_checks_continue(self):
        row = self.rows["login-page"]
        for error in (FileNotFoundError("Compiler missing"), subprocess.TimeoutExpired("tsc", 120),
                      RuntimeError("Compiler wrapper failed")):
            with self.subTest(error=error), patch.object(evaluators, "compile_check", side_effect=error):
                score, status = evaluators.compiles(row["inputs"], row["outputs"])
                self.assertEqual((score["score"], status["value"]), (0, "tool_error"))
                self.assertEqual(score["evaluator_info"]["error"]["type"], type(error).__name__)
                self.assertIsNone(score["evaluator_info"]["report"])
                self.assertEqual(evaluators.residue_free(row["inputs"], row["outputs"])[0]["score"], 1)

    def test_unusable_reports_cannot_be_mistaken_for_candidate_verdicts(self):
        row = self.rows["login-page"]
        reports = [None, ValidationReport(gate="residue", passed=True),
                   ValidationReport(gate="compile", passed=False, tool_output="unparsed fatal error"),
                   ValidationReport(gate="compile", passed=False, findings=[Finding(
                       gate="compile", file="pages/LoginPage.ts", code="validator-error", message="tsc missing")])]
        for report in reports:
            with self.subTest(report=report), patch.object(evaluators, "compile_check", return_value=report):
                score, status = evaluators.compiles(row["inputs"], row["outputs"])
                self.assertEqual((score["score"], status["value"]), (0, "tool_error"))
                self.assertIsNotNone(score["evaluator_info"]["error"])
                expected = report.model_dump(mode="json") if report is not None else None
                self.assertEqual(score["evaluator_info"]["report"], expected)

    def test_lint_warnings_keep_the_pass_and_all_diagnostic_evidence(self):
        row = self.rows["login-page"]
        report = ValidationReport(gate="lint", passed=True, findings=[Finding(
            gate="lint", file="pages/LoginPage.ts", line=7, column=3,
            code="warning/style", message="Review locator choice")], tool_output="raw warning details")
        with patch.object(evaluators, "lint_check", return_value=report):
            score, status = evaluators.typed_lint_pass(row["inputs"], row["outputs"])
        self.assertEqual((score["score"], status["value"]), (1, "passed"))
        self.assertIn("pages/LoginPage.ts:7:3 warning/style", score["comment"])
        self.assertEqual(score["evaluator_info"]["report"], report.model_dump(mode="json"))

    def test_sdk_binds_inputs_outputs_and_accepts_all_eight_metrics_locally(self):
        row = self.rows["login-test"]
        inputs, output = row["inputs"], row["outputs"] | {"report": {"status": "needs-review"}}
        run_id = uuid4()
        run = Run(id=run_id, trace_id=run_id, name="fixed-candidate", run_type="chain",
                  start_time=datetime.now(timezone.utc), inputs=inputs, outputs=output)
        # Deliberately unusable reference: these checks must bind inputs/outputs only.
        example = Example(id=uuid4(), inputs=inputs, outputs={"code": "REFERENCE MUST NOT BE GRADED"})
        keys = []
        for gate, evaluator in zip(evaluators.GATE_KEYS, evaluators.EVALUATORS, strict=True):
            report = ValidationReport(gate=gate, passed=True)
            with patch.object(evaluators, gate + "_check", return_value=report) as check:
                result = run_evaluator(evaluator).evaluate_run(run, example)
            candidate = {inputs["source_path"]: output["code"]}
            if gate == "parity":
                check.assert_called_once_with({inputs["source_path"]: inputs["source"]}, candidate)
            else:
                check.assert_called_once_with(inputs["context_files"] | candidate)
            score, status = result["results"]
            self.assertEqual((score.score, status.value), (1, "passed"))
            self.assertEqual(score.evaluator_info["report"], report.model_dump(mode="json"))
            keys.extend([score.key, status.key])
        self.assertEqual(len(set(keys)), 8)


if __name__ == "__main__":
    unittest.main()
