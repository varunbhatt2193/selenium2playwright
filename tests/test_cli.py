"""Step 8.1 — the s2p command line: the scorecard, the diff, and the seams.

Everything here is offline: the model is scripted, the four validation gates
are the real tools. What is under test is the *surface* — what a person sees,
on which stream, and with which exit code.
"""

import io
import os
import tomllib
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import typer
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from selenium2playwright import cli, graph
from selenium2playwright.schemas import ConversionResult, Critique

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "samples/selenium-suite/pages/LoginPage.ts"
GOLDEN = (ROOT / "samples/playwright-golden/pages/LoginPage.ts").read_text()
BROKEN = GOLDEN.replace("await this.usernameInput.fill", "this.usernameInput.fill")
PASS = Critique(verdict="pass", fixes=[])
REVISE = Critique(verdict="revise", fixes=["Await usernameInput.fill() to prevent a floating Promise."])


class CliHarness(unittest.TestCase):
    """A scripted model plus the real gates, driven through cli.run()."""

    def setUp(self):
        tracing = patch.dict(os.environ, {"LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false",
                                          # Shaped like a key, worth nothing: the CLI now builds the configured
                                          # model before running, and no test may need a real credential.
                                          "ANTHROPIC_API_KEY": "sk-ant-offline-test"})
        tracing.start()
        self.addCleanup(tracing.stop)
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def replies(self, drafts, reviews):
        queues = {ConversionResult: iter(drafts), Critique: iter(reviews)}

        def structured(schema, **kwargs):
            def respond(prompt):
                return {"parsed": next(queues[schema]), "parsing_error": None,
                        "raw": AIMessage(content="", usage_metadata={
                            "input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
                            "input_token_details": {"cache_read": 3}})}
            return RunnableLambda(respond)

        model = Mock()
        model.with_structured_output.side_effect = structured
        return patch.object(graph, "make_model", return_value=model)

    def run_cli(self, *argv):
        """Exit code, stdout, stderr — the three things a caller of s2p sees."""
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.run(list(argv))
        return code, out.getvalue(), err.getvalue()

    def convert(self, *argv, drafts=None, reviews=None):
        with self.replies(drafts or [ConversionResult(code=GOLDEN)], reviews or [PASS]):
            return self.run_cli("convert", str(SOURCE), *argv)

    def row(self, stderr: str, check: str) -> str:
        """The one scorecard *table* row naming this check, box characters and all.

        Filtered on the box character so that a prose line mentioning the same
        word (the verdict line says "critic" too) can never be mistaken for it.
        """
        found = [line for line in stderr.splitlines() if "\u2502" in line and check in line]
        self.assertTrue(found, f"no {check!r} row in:\n{stderr}")
        return found[0]


class ScorecardTests(CliHarness):
    def test_the_scorecard_names_every_gate_the_critic_and_the_open_todos(self):
        code, _, err = self.convert("--out", str(Path(self.tmp.name) / "LoginPage.ts"), "--no-diff")
        self.assertEqual(code, 0)
        self.assertIn("Conversion: passed (1/3 attempts)", err)
        for gate in ("compile", "residue", "lint", "parity"):
            self.assertIn("PASS", self.row(err, gate))
        self.assertIn("PASS", self.row(err, "critic"))
        self.assertIn("NONE", self.row(err, "TODO(review)"))

    def test_a_failed_gate_shows_in_its_row_and_the_finding_is_printed_in_full(self):
        """The row is the verdict; the line under it is the evidence for it."""
        _, _, err = self.convert("--max-attempts", "1", "--no-diff",
                                 drafts=[ConversionResult(code=BROKEN)], reviews=[REVISE])
        self.assertIn("FAIL", self.row(err, "lint"))
        self.assertIn("finding(s)", self.row(err, "lint"))
        self.assertIn("no-floating-promises", err)
        self.assertIn("REVISE", self.row(err, "critic"))
        self.assertIn("fix: Await usernameInput.fill()", err)

    def test_open_todos_are_counted_in_the_scorecard_and_listed(self):
        todo = "TODO(review): Verify the application's configured baseURL."
        draft = ConversionResult(code=GOLDEN + f"\n// {todo}\n", todos=[todo])
        code, _, err = self.convert("--no-diff", drafts=[draft])
        self.assertEqual(code, 1)  # a passing conversion with an open TODO is needs-review
        self.assertIn("needs a human", self.row(err, "TODO(review)"))
        self.assertIn(todo, err)

    def test_model_text_containing_brackets_is_never_read_as_rich_markup(self):
        """rich would eat "[data-testid]" as a style tag; say() prints it literally."""
        draft = ConversionResult(code=GOLDEN, notes=["kept the [data-testid] locators"])
        _, _, err = self.convert("--no-diff", drafts=[draft])
        self.assertIn("kept the [data-testid] locators", err)


class DiffTests(CliHarness):
    def test_the_before_after_diff_is_the_source_against_the_conversion(self):
        _, _, err = self.convert("--out", str(Path(self.tmp.name) / "LoginPage.ts"))
        self.assertIn("LoginPage.ts → converted", err)  # the panel title
        self.assertIn("@@", err)
        self.assertIn("-import { By, WebDriver", err)   # gone from the Playwright file
        self.assertIn("+import { type Locator, type Page }", err)  # arrived in it

    def test_no_diff_turns_the_panel_off_and_changes_nothing_else(self):
        _, _, quiet = self.convert("--no-diff")
        self.assertNotIn("@@", quiet)
        self.assertIn("Conversion: passed", quiet)

    def test_a_refine_turn_diffs_against_the_previous_turn_not_the_source(self):
        """On turn 2 the interesting question is what *this turn* changed."""
        db = Path(self.tmp.name) / "threads.sqlite"
        out = Path(self.tmp.name) / "LoginPage.ts"
        second = GOLDEN.replace('page.locator("#flash")', 'page.getByTestId("flash")')
        with self.replies([ConversionResult(code=GOLDEN), ConversionResult(code=second)], [PASS, PASS]):
            self.run_cli("convert", str(SOURCE), "--thread", "login", "--out", str(out),
                         "--db", str(db))
            _, _, err = self.run_cli("convert", "--thread", "login", "--refine",
                                     "use data-testid locators", "--db", str(db))
        self.assertIn("previous turn → converted", err)
        self.assertIn('+    this.flashMessage = page.getByTestId("flash");', err)
        self.assertNotIn("-import { By, WebDriver", err)  # the source is not the baseline now


class StreamAndExitCodeTests(CliHarness):
    def test_stdout_is_only_the_converted_file_so_it_can_be_piped(self):
        code, out, err = self.convert()
        self.assertEqual((code, out), (0, GOLDEN))
        self.assertIn("compile", err)  # everything a human reads went to stderr

    def test_an_unsupported_file_is_refused_with_exit_2_and_no_output(self):
        source = Path(self.tmp.name) / "wdio.ts"
        source.write_text('import { browser } from "webdriverio";')
        with patch.object(graph, "make_model") as model:
            code, out, err = self.run_cli("convert", str(source))
        self.assertEqual((code, out), (2, ""))
        self.assertIn("not converted", err)
        model.assert_not_called()

    def test_usage_errors_are_exit_2_and_say_what_to_do_instead(self):
        for argv, expected in (
            (["convert"], "give a source file"),
            (["convert", str(SOURCE), "--out", str(SOURCE)], "must differ from the source"),
            (["convert", str(SOURCE), "--max-attempts", "9"], "9 is not in the range"),
            (["convert", str(Path(self.tmp.name) / "missing.ts")], "does not exist"),
        ):
            with self.subTest(argv=argv):
                code, out, err = self.run_cli(*argv)
                self.assertEqual((code, out), (2, ""))
                self.assertIn(expected, err)


class SurfaceTests(unittest.TestCase):
    def test_the_console_script_points_at_the_typer_app(self):
        """`uv run s2p` has to resolve to something; pyproject is where it is declared."""
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
        self.assertEqual(pyproject["project"]["scripts"]["s2p"], "selenium2playwright.cli:app")

    def test_every_command_is_registered_and_documented(self):
        command = typer.main.get_command(cli.app)
        self.assertEqual(sorted(command.commands),
                         ["convert", "forget", "memories", "remember", "scan", "threads"])
        for name, sub in command.commands.items():
            self.assertTrue((sub.help or "").strip(), f"{name} has no help text")


if __name__ == "__main__":
    unittest.main()
