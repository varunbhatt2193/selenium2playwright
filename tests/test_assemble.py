"""Step 9.3 — assembling a converted folder into one deliverable.

Four facts are under test, and they are tested the way they are produced: the
whole-tree compile runs the real pinned `tsc`, the parity ledger runs the real
TypeScript parser, and the two ledgers that are pure text work are pure Python.
Nothing here reaches the network, and nothing executes converted code.

The test worth reading first is the one that builds two files which each compile
perfectly on their own and cannot compile together. That is the hole in
per-file validation that this whole step exists to close.
"""

import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from selenium2playwright import assemble, cli, suite, suite_graph
from selenium2playwright.classify import classify
from selenium2playwright.schemas import ConversionReport, ConversionResult, Critique, ValidationReport

ROOT = Path(__file__).resolve().parents[1]


def build(directory: Path, files: dict[str, str]) -> Path:
    for name, source in files.items():
        target = directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
    return directory


def outcome(path, wave=1, status="passed", written="", todos=(), notes=(), reason="", attempts=1):
    return suite_graph.FileOutcome(
        path=path, wave=wave, status=status, attempts=attempts, reason=reason,
        gates=tuple((gate, status == "passed") for gate in suite_graph.GATES),
        critic="pass" if status == "passed" else "revise",
        todos=tuple(todos), notes=tuple(notes), written=written, seconds=1.0)


class TreeCompileTests(unittest.TestCase):
    """Gate 5, and the only one that can see between files."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / "out"

    def test_two_files_that_each_compile_alone_can_still_fail_together(self):
        """The hole per-file validation cannot see, and the reason 9.3 exists.

        The page object takes two arguments; the spec calls it with one. Convert
        either file on its own, against the source it was given, and every gate
        is green. Compile the delivered folder as one project and it is broken.
        """
        build(self.out, {
            "pages/LoginPage.ts": (
                "export class LoginPage {\n"
                "  async login(user: string, password: string): Promise<void> {}\n"
                "}\n"),
            "tests/login.spec.ts": (
                'import { LoginPage } from "../pages/LoginPage";\n'
                'export async function run() { await new LoginPage().login("tomsmith"); }\n'),
        })
        report, error = assemble.compile_tree(assemble.read_tree(self.out))
        self.assertEqual(error, "")
        self.assertFalse(report.passed)
        self.assertEqual([f.file for f in report.findings], ["tests/login.spec.ts"])
        self.assertIn("Expected 2 arguments", report.findings[0].message)

    def test_a_tree_that_holds_together_passes(self):
        build(self.out, {
            "pages/LoginPage.ts": "export class LoginPage {\n  async login(user: string) {}\n}\n",
            "tests/login.spec.ts": ('import { LoginPage } from "../pages/LoginPage";\n'
                                    'export async function run() { await new LoginPage().login("x"); }\n'),
        })
        report, error = assemble.compile_tree(assemble.read_tree(self.out))
        self.assertTrue(report.passed, report.tool_output)
        self.assertEqual(error, "")

    def test_an_empty_tree_is_unknown_rather_than_fine(self):
        report, error = assemble.compile_tree({})
        self.assertIsNone(report)
        self.assertIn("no TypeScript files", error)
        # And "unknown" must never read as a pass.
        self.assertFalse(assemble.Assembly(tree=None, tree_error=error).compiles)

    def test_a_missing_compiler_is_reported_as_text_not_raised(self):
        """The conversion already happened; losing the run over a missing tool is worse."""
        with patch("selenium2playwright.validators.compile.compile_check",
                   side_effect=RuntimeError("tsc missing — run `npm install`")):
            report, error = assemble.compile_tree({"a.ts": "export const a = 1;\n"})
        self.assertIsNone(report)
        self.assertIn("tsc missing", error)

    def test_the_tree_is_read_without_dependencies_or_hidden_folders(self):
        build(self.out, {"pages/LoginPage.ts": "export const a = 1;\n",
                         "node_modules/pkg/index.ts": "export const b = 2;\n",
                         ".cache/old.ts": "export const c = 3;\n",
                         "notes.md": "not source\n"})
        self.assertEqual(list(assemble.read_tree(self.out)), ["pages/LoginPage.ts"])


class LedgerTests(unittest.TestCase):
    """The public surface, before and after — the real TypeScript parser."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "src"
        self.out = Path(self.tmp.name) / "out"

    def ledger(self, source: str, converted: str, path="pages/LoginPage.ts", **kwargs):
        build(self.root, {path: source})
        build(self.out, {path: converted})
        built, notes = assemble.ledgers(
            self.root, self.out, [outcome(path, written=str(self.out / path), **kwargs)])
        self.notes = notes
        return built[0]

    def test_kept_renamed_and_removed_are_told_apart(self):
        ledger = self.ledger(
            "export class LoginPage {\n"
            "  async open() {}\n"                 # kept
            "  async getFlashText() {}\n"         # renamed to flashMessage
            "  async dismissBanner() {}\n"        # removed
            "}\n",
            "export class LoginPage {\n"
            "  async open() {}\n"
            "  get flashMessage() { return 1; }\n"
            "  readonly usernameInput = 1;\n"     # added
            "}\n")
        verdicts = {change.name: change.verdict for change in ledger.changes}
        self.assertEqual(verdicts["LoginPage.open"], "kept")
        self.assertEqual(verdicts["LoginPage.getFlashText"], "renamed")
        self.assertEqual(verdicts["LoginPage.dismissBanner"], "removed")
        renamed = next(c for c in ledger.changes if c.verdict == "renamed")
        self.assertEqual(renamed.counterpart, "LoginPage.flashMessage")
        self.assertEqual(ledger.added, ("LoginPage.usernameInput",))
        self.assertEqual(ledger.unexplained, 1)

    def test_two_unrelated_leftovers_are_a_removal_and_an_addition_not_a_guessed_rename(self):
        """A wrong rename hides a loss, so the bar is high on purpose."""
        ledger = self.ledger(
            "export class LoginPage {\n  async dismissBanner() {}\n}\n",
            "export class LoginPage {\n  async waitForSpinner() {}\n}\n")
        self.assertEqual([c.verdict for c in ledger.changes], ["removed"])
        self.assertEqual(ledger.added, ("LoginPage.waitForSpinner",))

    def test_a_removal_the_model_explained_quotes_its_own_words(self):
        ledger = self.ledger(
            "export class LoginPage {\n  async open() {}\n  async waitForFlash() {}\n}\n",
            "export class LoginPage {\n  async open() {}\n}\n",
            notes=["waitForFlash was dropped: Playwright's web-first assertions wait by themselves"])
        removed = next(c for c in ledger.changes if c.verdict == "removed")
        self.assertIn("web-first assertions", removed.reason)
        self.assertEqual(ledger.unexplained, 0)  # explained is not the same as unexplained

    def test_a_renamed_class_is_not_every_member_disappearing(self):
        ledger = self.ledger(
            "export class LoginPage {\n  async open() {}\n  async login(u: string) {}\n}\n",
            "export class LoginPageObject {\n  async open() {}\n  async login(u: string) {}\n}\n")
        self.assertEqual(ledger.count("kept"), 2)
        self.assertEqual(ledger.losses, ())
        self.assertTrue(any("class LoginPage is now LoginPageObject" in note for note in self.notes))

    def test_private_and_protected_members_are_not_part_of_the_public_surface(self):
        ledger = self.ledger(
            "export class LoginPage {\n"
            "  constructor(public readonly url: string, private driver: object) {}\n"
            "  private secret() {}\n"
            "  protected helper() {}\n"
            "  async open() {}\n"
            "}\n",
            "export class LoginPage {\n"
            "  constructor(public readonly url: string) {}\n"
            "  async open() {}\n"
            "}\n")
        self.assertEqual(sorted(c.name for c in ledger.changes),
                         ["LoginPage.open", "LoginPage.url"])
        self.assertEqual(ledger.losses, ())

    def test_a_lost_test_is_a_removal_and_is_never_paired_with_a_method(self):
        ledger = self.ledger(
            'describe("Login", () => {\n'
            '  it("logs in", () => {});\n'
            '  it("rejects a bad password", () => {});\n'
            "});\n"
            "export function helper() {}\n",
            'import { test } from "@playwright/test";\n'
            'test.describe("Login", () => {\n'
            '  test("logs in", async () => {});\n'
            "});\n"
            "export function rejectsABadPassword() {}\n",
            path="tests/login.spec.ts")
        lost = [c for c in ledger.changes if c.verdict == "removed"]
        self.assertEqual(sorted(c.name for c in lost),
                         ["Login > rejects a bad password", "helper"])
        self.assertEqual([c.kind for c in lost if "Login >" in c.name], ["test"])
        # The added function is close enough to the lost test's title to tempt a
        # pairing; tests and members are matched in separate pools, so it cannot.
        self.assertIn("rejectsABadPassword", ledger.added)

    def test_a_file_that_was_never_written_says_why_instead_of_comparing_nothing(self):
        build(self.root, {"pages/LoginPage.ts": "export class LoginPage {}\n"})
        built, _ = assemble.ledgers(self.root, self.out, [
            outcome("pages/LoginPage.ts", status="refused", reason="webdriverio is not supported")])
        self.assertEqual(built[0].note, "webdriverio is not supported")
        self.assertEqual(built[0].changes, ())

    def test_an_inventory_that_cannot_run_is_a_note_not_a_crash(self):
        build(self.root, {"a.ts": "export const a = 1;\n"})
        build(self.out, {"a.ts": "export const a = 1;\n"})
        with patch.object(assemble, "node_inventory", side_effect=RuntimeError("node missing")):
            built, notes = assemble.ledgers(self.root, self.out,
                                            [outcome("a.ts", written=str(self.out / "a.ts"))])
        self.assertEqual(built[0].note, "the inventory could not be read")
        self.assertIn("node missing", notes[0])


class RenameTests(unittest.TestCase):
    """The one guess in this module, and where it stops guessing."""

    def test_an_idiom_rename_is_recognised_and_an_unrelated_name_is_not(self):
        for before, after in (("getFlashText", "flashMessage"), ("getHeadingText", "heading"),
                              ("getUploadedFilename", "uploadedFiles")):
            with self.subTest(rename=(before, after)):
                self.assertGreaterEqual(assemble.affinity(before, after), assemble.RENAME_RATIO)
        for before, after in (("open", "goto"), ("login", "logout"), ("open", "flashMessage")):
            with self.subTest(unrelated=(before, after)):
                self.assertLess(assemble.affinity(before, after), assemble.RENAME_RATIO)

    def test_members_of_different_classes_are_never_paired(self):
        self.assertEqual(assemble.qualified_affinity("LoginPage.getFlashText",
                                                     "CartPage.flashMessage"), 0.0)
        self.assertGreater(assemble.qualified_affinity("LoginPage.getFlashText",
                                                       "LoginPage.flashMessage"), 0.9)


class TodoLedgerTests(unittest.TestCase):
    """Playbook rule 25: every TODO in one list, once."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / "out"

    def test_the_same_task_reported_by_two_files_is_one_entry(self):
        """Observed live in 9.2: the spec repeats its page object's TODO, prefixed."""
        build(self.out, {
            "pages/LoginPage.ts": ("export class LoginPage {\n"
                                   "  // TODO(review): confirm the flash locator\n"
                                   "  async open() {}\n}\n"),
            "tests/login.spec.ts": "export const spec = 1;\n"})
        todos = assemble.todo_ledger(self.out, [
            outcome("pages/LoginPage.ts", written=str(self.out / "pages/LoginPage.ts"),
                    todos=["TODO(review): confirm the flash locator"]),
            outcome("tests/login.spec.ts", wave=2, written=str(self.out / "tests/login.spec.ts"),
                    todos=["pages/LoginPage.ts: TODO(review): confirm the flash locator"])])
        self.assertEqual(len(todos), 1)
        self.assertEqual(todos[0].text, "confirm the flash locator")
        # One task, but every place it was seen — including the line in the code.
        # `pages/LoginPage.ts:2` and a bare `pages/LoginPage.ts` are the same
        # sighting; the one with the line number wins.
        self.assertEqual(todos[0].places, ("pages/LoginPage.ts:2", "tests/login.spec.ts"))
        self.assertEqual(todos[0].files, ("pages/LoginPage.ts", "tests/login.spec.ts"))

    def test_a_todo_only_in_the_code_and_a_todo_only_in_the_report_are_both_listed(self):
        build(self.out, {"a.ts": "// TODO(review): written but never reported\nexport const a = 1;\n"})
        todos = assemble.todo_ledger(self.out, [
            outcome("a.ts", written=str(self.out / "a.ts"),
                    todos=["TODO(review): reported but never written"])])
        self.assertEqual([t.text for t in todos],
                         ["written but never reported", "reported but never written"])
        self.assertEqual(todos[0].places, ("a.ts:1",))
        self.assertEqual(todos[1].places, ("a.ts",))

    def test_a_wrapped_comment_is_one_task_and_matches_the_full_line_reported(self):
        """Seen live in 9.3: the code carries the first line, the report the paragraph."""
        build(self.out, {"pages/LoginPage.ts": (
            "export class LoginPage {\n"
            "  // TODO(review): original locators were By.id(\"username\").\n"
            "  // No DOM evidence was supplied, so the locator ladder could not\n"
            "  // be climbed; verify against the live page.\n"
            "  async open() {}\n}\n")})
        full = ("TODO(review): original locators were By.id(\"username\"). No DOM evidence was "
                "supplied, so the locator ladder could not be climbed; verify against the live page.")
        todos = assemble.todo_ledger(self.out, [
            outcome("pages/LoginPage.ts", written=str(self.out / "pages/LoginPage.ts"),
                    todos=[full])])
        self.assertEqual(len(todos), 1)
        self.assertTrue(todos[0].text.endswith("verify against the live page."))
        self.assertEqual(todos[0].places, ("pages/LoginPage.ts:2",))

    def test_the_same_task_written_two_ways_is_still_one_task(self):
        for text in ("// TODO(review): check the wait", "  /* TODO(review) check the wait */",
                     "pages/A.ts: TODO(review):   check   the wait"):
            with self.subTest(text=text):
                self.assertEqual(assemble.clean(text[text.index("TODO"):]), "check the wait")


class ReportTests(unittest.TestCase):
    """The document: the verdict a reader sees, and the JSON a build reads."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = build(Path(self.tmp.name) / "src",
                          {"pages/LoginPage.ts": "export class LoginPage {\n  async open() {}\n}\n"})
        self.out = build(Path(self.tmp.name) / "out",
                         {"pages/LoginPage.ts": "export class LoginPage {\n  async open() {}\n}\n"})
        self.manifest = suite.scan(self.root)
        self.outcomes = [outcome("pages/LoginPage.ts", written=str(self.out / "pages/LoginPage.ts"))]

    def render(self, built):
        return assemble.render(self.root, self.out, self.manifest, self.outcomes, built,
                               elapsed=12.5, models={"actor": "anthropic:claude-sonnet-5"},
                               when=datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc))

    def test_the_headline_leads_with_the_tree_verdict(self):
        built = assemble.assemble(self.root, self.out, self.manifest, self.outcomes)
        markdown = self.render(built)
        self.assertIn("the tree compiles as one project", markdown.splitlines()[2])
        self.assertIn("2026-09-06 12:00 UTC", markdown)

        broken = ValidationReport(gate="compile", passed=False, findings=[
            {"gate": "compile", "file": "tests/login.spec.ts", "line": 2, "code": "TS2554",
             "message": "Expected 2 arguments, but got 1."}])
        failing = self.render(assemble.Assembly(tree=broken, files=2, scorecard=built.scorecard))
        self.assertIn("the converted files do NOT compile (1 error(s))", failing.splitlines()[2])
        self.assertIn("TS2554", failing)

    def test_a_message_with_a_pipe_cannot_break_the_table(self):
        finding = {"gate": "compile", "file": "a.ts", "line": 1, "code": "TS1005",
                   "message": "Type 'a | b' is not\nassignable"}
        rows = self.render(assemble.Assembly(
            tree=ValidationReport(gate="compile", passed=False, findings=[finding]), files=1))
        row = next(line for line in rows.splitlines() if "TS1005" in line)
        self.assertEqual(row.count("|"), 6)  # five cells, no more
        self.assertIn("a \\| b", row)

    def test_report_json_keeps_every_key_of_the_run_document_and_adds_the_assembly(self):
        built = assemble.assemble(self.root, self.out, self.manifest, self.outcomes)
        run = suite_graph.run_json({"manifest": self.manifest, "out_root": str(self.out),
                                    "outcomes": self.outcomes, "waves": [["pages/LoginPage.ts"]],
                                    "elapsed": 12.5}, 0)
        document = assemble.report_json(run, built)
        for key in run:
            self.assertIn(key, document)
        self.assertEqual(document["schema"], assemble.REPORT_SCHEMA)
        self.assertEqual(document["counts"], run["counts"])
        self.assertTrue(document["tree"]["compiles"])
        self.assertEqual(document["parity"][0]["path"], "pages/LoginPage.ts")
        self.assertEqual(document["scorecard"]["gates"]["compile"], {"passed": 1, "of": 1})


class TreeVerdictSplitTests(unittest.TestCase):
    """Whole-tree errors are a verdict on the conversion only where it converted."""

    def finding(self, path, code="TS2307", message="Cannot find module 'selenium-webdriver'."):
        return {"gate": "compile", "file": path, "line": 1, "code": code, "message": message}

    def manifest_of(self, converted, carried):
        files = []
        for path in converted + carried:
            source = ('import { WebDriver } from "selenium-webdriver";\n'
                      f"export class C{len(files)} {{}}\n")
            files.append(suite.SuiteFile(
                path=path, kind="page-object",
                action=suite.CONVERT if path in converted else suite.COPY,
                reason="", classification=classify(source, path)))
        return suite.Manifest(root="r", files=tuple(files), waves=())

    def test_errors_only_in_carried_selenium_are_not_a_failed_conversion(self):
        """The cap's own doing: unconverted Selenium cannot compile, and never could."""
        tree = ValidationReport(gate="compile", passed=False, findings=[
            self.finding("pages/Left.ts"), self.finding("pages/AlsoLeft.ts")])
        outcomes = [outcome("pages/Done.ts")]
        manifest = self.manifest_of(["pages/Done.ts"], ["pages/Left.ts", "pages/AlsoLeft.ts"])
        split = assemble.split_tree_findings(tree, outcomes, manifest)
        self.assertEqual(len(split["converted"]), 0)
        self.assertEqual(len(split["unconverted"]), 2)
        self.assertEqual(len(split["companion"]), 0)

    def test_an_error_in_a_converted_file_still_counts(self):
        tree = ValidationReport(gate="compile", passed=False, findings=[
            self.finding("pages/Done.ts", "TS2554", "Expected 2 arguments, but got 1.")])
        manifest = self.manifest_of(["pages/Done.ts"], ["pages/Left.ts"])
        split = assemble.split_tree_findings(tree, [outcome("pages/Done.ts")], manifest)
        self.assertEqual(len(split["converted"]), 1)

    def test_a_carried_file_with_no_selenium_left_is_our_problem(self):
        """The caller whose companion's API moved under it — never quietly excused."""
        tree = ValidationReport(gate="compile", passed=False, findings=[
            self.finding("pages/Caller.ts", "TS2339", "Property 'driver' does not exist.")])
        files = (suite.SuiteFile(path="pages/Done.ts", kind="page-object", action=suite.CONVERT,
                                 reason="", classification=classify("import x from 'selenium-webdriver';", "a.ts")),
                 suite.SuiteFile(path="pages/Caller.ts", kind="support", action=suite.COPY,
                                 reason="", classification=classify("export const a = 1;\n", "b.ts")))
        split = assemble.split_tree_findings(
            tree, [outcome("pages/Done.ts")], suite.Manifest(root="r", files=files, waves=()))
        self.assertEqual(len(split["companion"]), 1)
        self.assertEqual(len(split["unconverted"]), 0)

    def test_a_missing_split_means_every_error_is_ours(self):
        """Fail closed: silence about ownership must never render as a green tree."""
        tree = ValidationReport(gate="compile", passed=False, findings=[
            self.finding("anything.ts", "TS2554", "Expected 2 arguments, but got 1.")])
        mine, carried = assemble.owned_findings(assemble.Assembly(tree=tree, files=1))
        self.assertEqual(len(mine), 1)
        self.assertEqual(carried, ())


class CapNoteInReportTests(unittest.TestCase):
    """When the demo's cap bites, the report says so where the files are listed."""

    def test_section_three_leads_with_the_cap_and_the_way_around_it(self):
        root = Path(__file__).resolve().parents[1] / "samples/selenium-hard-suite"
        with patch.dict(os.environ, {"S2P_SUITE_MAX_TESTS": "3",
                                     "S2P_SUITE_MAX_PAGE_OBJECTS": "3"}, clear=False):
            manifest = suite.scan(root)
        markdown = assemble.render(root, Path("/nowhere"), manifest, [],
                                   assemble.Assembly(files=0))
        section = markdown.split("## 3.")[1].split("## 4.")[0]
        self.assertIn("converts at most 3 page objects and 3 test files", section)
        self.assertIn("your own LLM API key", section)
        # And the files it left out are named, not just counted.
        self.assertIn("past this demo's limit of 3 page objects per run", section)

    def test_no_cap_means_no_paragraph(self):
        root = Path(__file__).resolve().parents[1] / "samples/selenium-hard-suite"
        with patch.dict(os.environ, {"S2P_SUITE_MAX_TESTS": "",
                                     "S2P_SUITE_MAX_PAGE_OBJECTS": ""}, clear=False):
            manifest = suite.scan(root)
        markdown = assemble.render(root, Path("/nowhere"), manifest, [],
                                   assemble.Assembly(files=0))
        self.assertNotIn("your own LLM API key", markdown)


class CommandTests(unittest.TestCase):
    """`s2p suite` end to end, with the per-file graph scripted."""

    TOY = {
        "pages/LoginPage.ts": ('import { WebDriver } from "selenium-webdriver";\n'
                               "export class LoginPage {\n"
                               "  constructor(private driver: WebDriver) {}\n"
                               "  async login(user: string, password: string) {}\n"
                               "}\n"),
        "tests/login.spec.ts": ('import { Builder } from "selenium-webdriver";\n'
                                'import { LoginPage } from "../pages/LoginPage";\n'
                                'describe("Login", () => { it("works", () => new Builder()); });\n'),
    }

    # Each of these compiles perfectly on its own; together they do not.
    CONVERTED = {
        "pages/LoginPage.ts": ("export class LoginPage {\n"
                               "  async login(user: string, password: string): Promise<void> {}\n"
                               "}\n"),
        "tests/login.spec.ts": ('import { LoginPage } from "../pages/LoginPage";\n'
                                'export async function run() { await new LoginPage().login("x"); }\n'),
    }

    def setUp(self):
        keys = patch.dict(os.environ, {"LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false",
                                       "ANTHROPIC_API_KEY": "sk-ant-offline-test",
                                       "S2P_EMBEDDINGS": "off"})
        keys.start()
        self.addCleanup(keys.stop)
        width = cli.console._width  # rich folds cells to the terminal; pin it (see 9.1)
        cli.console.width = 100
        self.addCleanup(setattr, cli.console, "_width", width)
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = build(Path(self.tmp.name) / "src", self.TOY)
        self.out = Path(self.tmp.name) / "out"

    def run_cli(self, *argv, code_for=None):
        code_for = code_for or (lambda path: "export const converted = true;\n")

        class Child:
            def invoke(self, inputs, config=None, context=None):
                path = Path(inputs["source_path"])
                return {"status": "converted", "iteration": 1, "report": ConversionReport(
                    status="passed", attempts=1, reason="scripted",
                    result=ConversionResult(code=code_for(path.name)),
                    validation=[ValidationReport(gate=gate, passed=True)
                                for gate in suite_graph.GATES],
                    critique=Critique(verdict="pass", fixes=[]))}

        out, err = io.StringIO(), io.StringIO()
        with patch.object(suite_graph.single, "build_graph", side_effect=lambda *a, **k: Child()), \
                redirect_stdout(out), redirect_stderr(err):
            status = cli.run(["suite", *argv])
        return status, out.getvalue(), err.getvalue()

    def test_twelve_green_rows_still_exit_one_when_the_tree_does_not_compile(self):
        """Every file passed its own gates. The folder is still broken, and it says so."""
        names = {"LoginPage.ts": "pages/LoginPage.ts", "login.spec.ts": "tests/login.spec.ts"}
        code, _, err = self.run_cli(str(self.root), "--out", str(self.out),
                                    code_for=lambda name: self.CONVERTED[names[name]])
        self.assertEqual(code, 1, err)
        self.assertIn("2 file(s): 2 passed", err)  # the per-file verdict is unchanged…
        self.assertIn("Whole tree: 1 error(s) compiling 2 file(s) together", err)  # …and not enough
        self.assertIn("TS2554", err)

    def test_a_clean_run_writes_the_report_next_to_the_code_and_names_it(self):
        code, _, err = self.run_cli(str(self.root), "--out", str(self.out))
        self.assertEqual(code, 0, err)
        report = self.out / assemble.REPORT_NAME
        self.assertIn(f"[wrote {report}]", err)
        self.assertIn("Whole tree: 2 file(s) compile together, no errors", err)
        text = report.read_text()
        self.assertIn("# Conversion report — src", text)
        self.assertIn("## 5. TODO(review) ledger", text)
        self.assertIn("No file carries an open `TODO(review)`.", text)

    def test_the_report_can_be_written_somewhere_else_entirely(self):
        elsewhere = Path(self.tmp.name) / "reports" / "run.md"
        code, _, err = self.run_cli(str(self.root), "--out", str(self.out),
                                    "--report", str(elsewhere))
        self.assertEqual(code, 0, err)
        self.assertTrue(elsewhere.exists())
        self.assertFalse((self.out / assemble.REPORT_NAME).exists())

    def test_json_carries_the_assembled_document_and_the_table_stays_on_stderr(self):
        code, out, err = self.run_cli(str(self.root), "--out", str(self.out), "--json")
        self.assertEqual(code, 0, err)
        document = json.loads(out)
        self.assertEqual(document["schema"], assemble.REPORT_SCHEMA)
        self.assertTrue(document["tree"]["compiles"])
        self.assertEqual(document["tree"]["files"], 2)
        self.assertEqual(document["report"], str(self.out / assemble.REPORT_NAME))
        self.assertEqual(len(document["parity"]), 2)
        self.assertNotIn("PASS", out)
        self.assertRegex(err, r"pages/LoginPage\.ts[^\n]*PASS")


if __name__ == "__main__":
    unittest.main()
