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

from selenium2playwright import cli, graph
from selenium2playwright.prompts import format_context
from selenium2playwright.schemas import ConversionResult, Critique, Finding, ValidationReport

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "samples/selenium-suite"
GOLDEN = ROOT / "samples/playwright-golden"
POM = "pages/LoginPage.ts"
TEST = "tests/login.spec.ts"


class GatesJudgeTheConvertedFileTests(unittest.TestCase):
    """A companion's contents are not a verdict on this file's conversion.

    In a suite run the companions handed to `validate` are the folder's own
    untouched source, so they are full of Selenium. Residue and lint used to
    read them and fail a clean Playwright file for the file next door.
    """

    def state(self, converted_code, companion_code):
        return {
            "output_path": "/w/pages/Index.ts",
            "source_path": "/w/pages/Index.ts",
            "source": "import { By } from 'selenium-webdriver';\nexport class I {}\n",
            "context_files": {"/w/pages/login.page.ts": companion_code},
            "result": ConversionResult(code=converted_code, notes=(), todos=()),
        }

    def test_selenium_in_a_companion_does_not_fail_the_converted_file(self):
        clean = ('import { Page } from "@playwright/test";\n'
                 "export default class Index {\n"
                 "  constructor(private readonly page: Page) {}\n"
                 "}\n")
        seleniumy = ('import { By, WebDriver } from "selenium-webdriver";\n'
                     "export class Login {\n"
                     "  constructor(private driver: WebDriver) {}\n"
                     "  async go() { await this.driver.findElement(By.css('#a')).click(); }\n"
                     "}\n")
        out = graph.validate(self.state(clean, seleniumy))
        gates = {r.gate: r.passed for r in out["validation"]}
        self.assertTrue(gates["residue"], "a companion's Selenium is not this file's residue")
        self.assertTrue(gates["lint"], "a companion's style is not this file's lint")

    def test_selenium_in_the_converted_file_still_fails(self):
        """The gate must not have been softened into uselessness."""
        left_behind = ('import { By } from "selenium-webdriver";\n'
                       "export const a = By.css('#x');\n")
        out = graph.validate(self.state(left_behind, "export const b = 1;\n"))
        gates = {r.gate: r.passed for r in out["validation"]}
        self.assertFalse(gates["residue"])


class GraphValidationTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false",
                                      # Shaped like a key, worth nothing: the CLI now builds the configured
                                      # model before running, and no test may need a real credential.
                                      "ANTHROPIC_API_KEY": "sk-ant-offline-test"})
        env.start()
        self.addCleanup(env.stop)

    def model_reply(self, code):
        model = Mock()
        def structured(schema, **kwargs):
            parsed = Critique(verdict="pass", fixes=[]) if schema is Critique else ConversionResult(code=code)
            return RunnableLambda(lambda prompt: {
                "parsed": parsed, "raw": AIMessage(content=""), "parsing_error": None,
            })
        model.with_structured_output.side_effect = structured
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
                self.assertEqual(final["critique"].verdict, "pass")

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
            result = cli.run(["convert", str(SOURCE / POM), "--out", str(output)])
            self.assertEqual(result, 1)
            self.assertEqual(output.read_text(), code)
        self.assertEqual(stdout.getvalue(), "")
        for gate, verdict in (("compile", "PASS"), ("residue", "PASS"), ("lint", "FAIL"), ("parity", "PASS")):
            self.assertRegex(stderr.getvalue(), rf"{gate}[^\n]*{verdict}")
        self.assertIn("no-floating-promises", stderr.getvalue())

    def test_cli_stdout_is_only_code_and_warnings_do_not_fail(self):
        code = (GOLDEN / POM).read_text()
        warning = ValidationReport(gate="lint", passed=True, findings=[Finding(
            gate="lint", file=POM, code="warning/style", message="Review locator choice")])
        stdout, stderr = io.StringIO(), io.StringIO()
        with self.model_reply(code), patch.object(graph, "lint_check", return_value=warning), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(cli.run(["convert", str(SOURCE / POM), "--no-diff"]), 0)
        self.assertEqual(stdout.getvalue(), code)
        self.assertRegex(stderr.getvalue(), r"lint[^\n]*PASS[^\n]*1 finding\(s\)")
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
            self.assertEqual(cli.run(["convert", str(source)]), 2)
            model.assert_not_called()
            validate.assert_not_called()


class CarriedCompanionTests(unittest.TestCase):
    """Companions the run never converted must not be introduced as converted.

    Past the demo's cap a suite copies files across untouched, so the "already
    converted" companion a file was handed was raw Selenium. On a live run the
    critic did exactly what that prompt asked: it compared the conversion
    against its Selenium neighbour, found the mismatch, and voted revise on
    three files out of three, every lap. The files still have to be sent —
    `tsc` cannot resolve an import to a file it was not given — so the prompt
    says which are which instead.
    """

    def setUp(self):
        self.folder = TemporaryDirectory()
        base = Path(self.folder.name)
        self.done = base / "LoginPage.ts"
        self.done.write_text("export class LoginPage { constructor(public page: Page) {} }\n")
        self.left = base / "BasePage.ts"
        self.left.write_text('import { WebDriver } from "selenium-webdriver";\n')
        self.addCleanup(self.folder.cleanup)

    def test_a_converted_companion_is_still_offered_as_the_api_to_match(self):
        text = format_context([self.done])
        self.assertIn("ALREADY converted", text)
        self.assertIn("<converted_file", text)
        self.assertNotIn("<unconverted_file", text)

    def test_a_carried_companion_is_labelled_and_fenced_off(self):
        text = format_context([self.done, self.left], carried=[str(self.left)])
        self.assertIn(f'<converted_file path="{self.done}"', text)
        self.assertIn(f'<unconverted_file path="{self.left}"', text)
        self.assertIn("were NOT converted", text)
        self.assertIn("must not be reported", text)
        # The one that matters: the Selenium file is never inside a block the
        # model is told to treat as the target API.
        converted_block = text.split("NOT converted")[0]
        self.assertNotIn("BasePage.ts", converted_block)

    def test_all_carried_means_no_converted_section_at_all(self):
        text = format_context([self.left], carried=[str(self.left)])
        self.assertNotIn("ALREADY converted", text)
        self.assertIn("<unconverted_file", text)

    def test_intake_passes_the_carried_list_through_to_the_prompt(self):
        state = graph.intake({"source_path": str(SOURCE / POM),
                              "context_paths": [str(self.done), str(self.left)],
                              "carried_paths": [str(self.left)]})
        self.assertIn("<unconverted_file", state["context"])
        self.assertEqual(state["context"],
                         format_context([self.done, self.left], carried=[str(self.left)]))

    def test_no_carried_list_leaves_every_earlier_caller_unchanged(self):
        state = graph.intake({"source_path": str(SOURCE / POM),
                              "context_paths": [str(self.done)]})
        self.assertEqual(state["context"], format_context([self.done]))


if __name__ == "__main__":
    unittest.main()
