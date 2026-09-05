"""Exercise the real graph and validators with a fixed model reply; no API calls."""

import io
import os
import subprocess
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from selenium2playwright import graph
from selenium2playwright.prompts import format_context
from selenium2playwright.schemas import ConversionResult, Finding, ValidationReport

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "samples/selenium-suite"
GOLDEN = ROOT / "samples/playwright-golden"
POM = "pages/LoginPage.ts"
TEST = "tests/login.spec.ts"


class GraphValidationTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false"})
        env.start()
        self.addCleanup(env.stop)

    def model_reply(self, code):
        model = Mock()
        model.with_structured_output.return_value = RunnableLambda(lambda prompt: {
            "parsed": ConversionResult(code=code), "raw": AIMessage(content=""), "parsing_error": None,
        })
        return patch.object(graph, "make_model", return_value=model)

    def inputs(self, relative=POM):
        inputs = {"source_path": str(SOURCE / relative), "output_path": str(GOLDEN / relative)}
        if relative == TEST:
            inputs["context_paths"] = [str(GOLDEN / POM)]
        return inputs

    def test_one_invoke_runs_all_four_gates_on_pom_and_test_with_companion(self):
        for relative in (POM, TEST):
            code = (GOLDEN / relative).read_text()
            with self.subTest(file=relative), self.model_reply(code):
                final = graph.build_graph().invoke(self.inputs(relative))
                self.assertEqual(final["status"], "converted")
                self.assertEqual(final["result"].code, code)
                self.assertEqual([r.gate for r in final["validation"]], ["compile", "residue", "lint", "parity"])
                self.assertTrue(all(r.passed for r in final["validation"]), final["validation"])

    def test_assertion_loss_reaches_parity_after_compile_passes(self):
        code = (GOLDEN / TEST).read_text().replace(
            '    await expect(loginPage.flashMessage).toContainText(\n'
            '      "Your password is invalid!"\n    );', '')
        with self.model_reply(code):
            final = graph.build_graph().invoke(self.inputs(TEST))
        reports = {r.gate: r for r in final["validation"]}
        self.assertTrue(reports["compile"].passed)
        self.assertFalse(reports["parity"].passed)
        self.assertIn("rejects invalid credentials", reports["parity"].findings[0].message)

    def test_cli_keeps_code_and_returns_failure_for_dropped_await(self):
        code = (GOLDEN / POM).read_text().replace("await this.usernameInput.fill", "this.usernameInput.fill")
        stdout, stderr = io.StringIO(), io.StringIO()
        with TemporaryDirectory() as folder, self.model_reply(code), redirect_stdout(stdout), redirect_stderr(stderr):
            output = Path(folder) / "pages/LoginPage.ts"
            result = graph.main([str(SOURCE / POM), "--out", str(output)])
            self.assertEqual(result, 1)
            self.assertEqual(output.read_text(), code)
        self.assertEqual(stdout.getvalue(), "")
        for row in ("PASS compile", "PASS residue", "FAIL lint", "PASS parity"):
            self.assertIn(row, stderr.getvalue())
        self.assertIn("no-floating-promises", stderr.getvalue())

    def test_cli_stdout_is_only_code_and_warnings_do_not_fail(self):
        code = (GOLDEN / POM).read_text()
        warning = ValidationReport(gate="lint", passed=True, findings=[Finding(
            gate="lint", file=POM, code="warning/style", message="Review locator choice")])
        stdout, stderr = io.StringIO(), io.StringIO()
        with self.model_reply(code), patch.object(graph, "lint_check", return_value=warning), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(graph.main([str(SOURCE / POM)]), 0)
        self.assertEqual(stdout.getvalue(), code)
        self.assertIn("PASS lint: 1 finding(s)", stderr.getvalue())
        self.assertIn("Review locator choice", stderr.getvalue())

    def test_tool_failures_are_reports_and_remaining_gates_still_run(self):
        state = graph.intake(self.inputs()) | self.inputs() | {"result": ConversionResult(code=(GOLDEN / POM).read_text())}
        for error in (FileNotFoundError("tsc missing"), subprocess.TimeoutExpired("tsc", 120), RuntimeError("tool broke")):
            with self.subTest(error=error), patch.object(graph, "compile_check", side_effect=error):
                reports = graph.validate(state)["validation"]
                self.assertFalse(reports[0].passed)
                self.assertEqual(reports[0].findings[0].code, "validator-error")
                self.assertEqual(len(reports), 4)
                self.assertTrue(all(r.passed for r in reports[1:]))

    def test_companion_snapshot_survives_disk_changes_and_preserves_imports(self):
        with TemporaryDirectory() as folder:
            companion = Path(folder) / POM
            companion.parent.mkdir(parents=True)
            companion.write_text((GOLDEN / POM).read_text())
            inputs = {"source_path": str(SOURCE / TEST), "context_paths": [str(companion)],
                      "output_path": str(Path(folder) / TEST)}
            state = inputs | graph.intake(inputs)
            self.assertEqual(state["context"], format_context([companion]))
            companion.write_text("export class WrongPage {}")
            state["result"] = ConversionResult(code=(GOLDEN / TEST).read_text())
            self.assertTrue(all(r.passed for r in graph.validate(state)["validation"]))

    def test_missing_companion_is_an_honest_compile_failure(self):
        inputs = self.inputs(TEST)
        inputs["context_paths"] = []
        with self.model_reply((GOLDEN / TEST).read_text()):
            final = graph.build_graph().invoke(inputs)
        self.assertEqual(len(final["validation"]), 4)
        self.assertFalse(final["validation"][0].passed)
        self.assertIn("TS2307", [f.code for f in final["validation"][0].findings])

    def test_refusal_bypasses_model_and_validation(self):
        with TemporaryDirectory() as folder, patch.object(graph, "make_model") as model, \
                patch.object(graph, "validate") as validate, redirect_stderr(io.StringIO()):
            source = Path(folder) / "wdio.ts"
            source.write_text('import { browser } from "webdriverio";')
            self.assertEqual(graph.main([str(source)]), 2)
            model.assert_not_called()
            validate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
