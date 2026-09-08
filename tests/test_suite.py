"""Step 9.1 — the suite scanner: what is in the folder, and in what order.

Nothing here calls a model or reaches the network. The scanner is a pure
function of bytes on disk, so every test either points it at the real sample
suite or builds a toy one in a temp directory and reads the plan back.
"""

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from selenium2playwright import cli, suite

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "samples/selenium-suite"

# A six-file suite with one of everything, so the four kinds and a three-deep
# import chain are all exercised: BasePage → LoginPage → the spec.
TOY = {
    "pages/BasePage.ts": (
        'import { WebDriver, By } from "selenium-webdriver";\n'
        "export class BasePage {\n"
        "  constructor(protected driver: WebDriver) {}\n"
        "  async find(locator: By) { return this.driver.findElement(locator); }\n"
        "}\n"),
    "pages/LoginPage.ts": (
        'import { By, WebDriver } from "selenium-webdriver";\n'
        'import { BasePage } from "./BasePage";\n'
        "export class LoginPage extends BasePage {\n"
        "  async open() { await this.driver.get('/login'); }\n"
        "}\n"),
    "tests/login.spec.ts": (
        'import { Builder } from "selenium-webdriver";\n'
        'import { expect } from "chai";\n'
        'import { LoginPage } from "../pages/LoginPage";\n'
        'import { USERS } from "../support/users";\n'
        'describe("Login", () => { it("works", async () => { expect(USERS).to.exist; }); });\n'),
    "support/users.ts": 'export const USERS = { tomsmith: "SuperSecretPassword!" };\n',
    "legacy/old.cy.ts": 'describe("old", () => { cy.visit("/login"); });\n',
    "tests/smoke.spec.ts": (
        'import { test, expect } from "@playwright/test";\n'
        'test("already converted", async ({ page }) => { await page.goto("/"); });\n'),
}


def build(directory: Path, files: dict[str, str]) -> Path:
    for name, source in files.items():
        target = directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
    return directory


class ToySuite(unittest.TestCase):
    """A folder holding one file of every kind the scanner has to tell apart."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = build(Path(self.tmp.name), TOY)
        self.manifest = suite.scan(self.root)
        self.by_path = {f.path: f for f in self.manifest.files}

    def test_every_source_file_is_in_the_manifest(self):
        self.assertEqual(len(self.manifest.files), 6)
        self.assertEqual([f.path for f in self.manifest.files], sorted(TOY))

    def test_kinds_and_actions(self):
        expected = {
            "pages/BasePage.ts": ("page-object", suite.CONVERT),
            "pages/LoginPage.ts": ("page-object", suite.CONVERT),
            "tests/login.spec.ts": ("test", suite.CONVERT),
            "support/users.ts": ("support", suite.COPY),
            "legacy/old.cy.ts": ("unsupported", suite.SKIP),
            "tests/smoke.spec.ts": ("unsupported", suite.SKIP),
        }
        actual = {p: (f.kind, f.action) for p, f in self.by_path.items()}
        self.assertEqual(actual, expected)

    def test_a_helper_with_no_automation_is_copied_not_refused(self):
        # classify() says "unsupported" to this file on its own; in a folder
        # that means nothing to convert, which is a copy, not a failure.
        helper = self.by_path["support/users.ts"]
        self.assertFalse(helper.classification.supported)
        self.assertEqual(helper.action, suite.COPY)
        self.assertIn("carried over", helper.reason)

    def test_an_already_playwright_file_is_skipped_with_its_reason(self):
        smoke = self.by_path["tests/smoke.spec.ts"]
        self.assertEqual(smoke.action, suite.SKIP)
        self.assertIn("already a Playwright file", smoke.reason)

    def test_waves_follow_the_import_chain(self):
        self.assertEqual(self.manifest.waves, (
            ("pages/BasePage.ts",),
            ("pages/LoginPage.ts",),
            ("tests/login.spec.ts",),
        ))
        self.assertEqual([self.by_path[p].wave for p in
                          ("pages/BasePage.ts", "pages/LoginPage.ts", "tests/login.spec.ts")],
                         [1, 2, 3])

    def test_copied_and_skipped_files_are_in_no_wave(self):
        for path in ("support/users.ts", "legacy/old.cy.ts", "tests/smoke.spec.ts"):
            self.assertEqual(self.by_path[path].wave, 0, path)
        self.assertNotIn("support/users.ts", [p for wave in self.manifest.waves for p in wave])

    def test_imports_record_both_directions(self):
        spec = self.by_path["tests/login.spec.ts"]
        # The helper is an in-suite import even though it is never converted:
        # the edge is real, it just does not create a wait.
        self.assertEqual(spec.imports, ("pages/LoginPage.ts", "support/users.ts"))
        self.assertEqual(self.by_path["pages/LoginPage.ts"].imported_by, ("tests/login.spec.ts",))
        self.assertEqual(self.by_path["pages/BasePage.ts"].imported_by, ("pages/LoginPage.ts",))

    def test_packages_are_kept_separate_from_in_suite_files(self):
        spec = self.by_path["tests/login.spec.ts"]
        self.assertEqual(spec.external_imports, ("selenium-webdriver", "chai"))

    def test_counts_and_line_numbers(self):
        self.assertEqual(self.manifest.counts(), {"convert": 3, "copy": 1, "skip": 2})
        self.assertEqual(self.by_path["support/users.ts"].lines, 1)

    def test_the_manifest_is_reproducible(self):
        again = suite.scan(self.root)
        self.assertEqual(suite.manifest_json(self.manifest), suite.manifest_json(again))


class TextTree(unittest.TestCase):
    """The same plan from a dict of text as from the folder it would be.

    `scan_sources` exists so the guard can price an upload without writing it
    to disk; the only thing that can go wrong is disagreeing with `scan`.
    """

    def test_scan_sources_matches_scan(self):
        with TemporaryDirectory() as tmp:
            on_disk = suite.scan(build(Path(tmp), TOY))
        as_text = suite.scan_sources(TOY)
        strip = lambda m: [(f.path, f.kind, f.action, f.imports, f.wave) for f in m.files]  # noqa: E731
        self.assertEqual(strip(as_text), strip(on_disk))
        self.assertEqual(as_text.waves, on_disk.waves)

    def test_junk_directories_and_non_source_files_are_left_out_like_discover_does(self):
        tree = {**TOY, "node_modules/x/index.ts": TOY["pages/BasePage.ts"],
                "README.md": "# hi", "package.json": "{}"}
        self.assertEqual([f.path for f in suite.scan_sources(tree).files], sorted(TOY))

    def test_conversions_counts_what_reaches_a_model(self):
        # Six files: three convert, one is copied, two are skipped. The bill is three.
        self.assertEqual(suite.conversions(TOY), 3)
        self.assertEqual(suite.conversions(TOY, ["pages/*"]), 2)
        self.assertEqual(suite.conversions(TOY, ["login.spec.ts"]), 1)
        self.assertEqual(suite.conversions(TOY, ["nothing-matches"]), 0)
        self.assertEqual(suite.conversions({"a.ts": "export const A = 1;\n"}), 0)


class RealSuite(unittest.TestCase):
    """The suite the whole project demos on: six page objects, six tests."""

    def setUp(self):
        self.manifest = suite.scan(SAMPLE)

    def test_two_waves_page_objects_first(self):
        self.assertEqual(len(self.manifest.waves), 2)
        first, second = self.manifest.waves
        self.assertTrue(all(p.startswith("pages/") for p in first), first)
        self.assertTrue(all(p.startswith("tests/") for p in second), second)

    def test_everything_is_convertible_typescript_selenium(self):
        self.assertEqual(self.manifest.counts(), {"convert": 12, "copy": 0, "skip": 0})
        for item in self.manifest.files:
            self.assertEqual(item.classification.automation, "selenium", item.path)
            self.assertEqual(item.classification.language, "typescript", item.path)

    def test_each_test_waits_for_its_own_page_object(self):
        by_path = {f.path: f for f in self.manifest.files}
        self.assertEqual(by_path["tests/login.spec.ts"].imports, ("pages/LoginPage.ts",))
        self.assertEqual(by_path["pages/LoginPage.ts"].imported_by, ("tests/login.spec.ts",))


class Discovery(unittest.TestCase):
    def test_skips_dependency_and_output_directories(self):
        with TemporaryDirectory() as tmp:
            root = build(Path(tmp), {
                "pages/LoginPage.ts": TOY["pages/BasePage.ts"],
                "node_modules/chai/index.ts": "export const x = 1;\n",
                "out/pages/LoginPage.ts": "export const y = 1;\n",
                "notes.md": "not source\n",
            })
            found = [p.relative_to(root).as_posix() for p in suite.discover(root)]
        self.assertEqual(found, ["pages/LoginPage.ts"])

    def test_an_empty_folder_is_an_honest_empty_plan(self):
        with TemporaryDirectory() as tmp:
            manifest = suite.scan(Path(tmp))
        self.assertEqual(manifest.files, ())
        self.assertEqual(manifest.waves, ())
        self.assertIn("no TypeScript or JavaScript source files", manifest.notes[0])

    def test_scanning_a_file_is_refused(self):
        with self.assertRaises(NotADirectoryError):
            suite.scan(SAMPLE / "pages/LoginPage.ts")


class Imports(unittest.TestCase):
    def test_every_import_form_is_seen_once(self):
        source = ('import { A } from "./a";\n'
                  'import "./side-effect";\n'
                  'export { B } from "./b";\n'
                  'const c = require("./c");\n'
                  'const d = await import("./d");\n'
                  'import { A2 } from "./a";\n')
        self.assertEqual(suite.import_specifiers(source),
                         ["./a", "./side-effect", "./b", "./c", "./d"])

    def test_resolution_tries_the_typescript_endings(self):
        known = {"pages/BasePage.ts", "support/index.ts", "data/users.json"}
        resolve = suite.resolve_import
        self.assertEqual(resolve("./BasePage", "pages/LoginPage.ts", known), "pages/BasePage.ts")
        self.assertEqual(resolve("../support", "pages/LoginPage.ts", known), "support/index.ts")
        self.assertEqual(resolve("../data/users.json", "pages/LoginPage.ts", known),
                         "data/users.json")

    def test_a_package_or_an_outside_path_is_not_an_in_suite_import(self):
        known = {"pages/BasePage.ts"}
        self.assertIsNone(suite.resolve_import("selenium-webdriver", "pages/LoginPage.ts", known))
        self.assertIsNone(suite.resolve_import("node:os", "pages/LoginPage.ts", known))
        self.assertIsNone(suite.resolve_import("../../shared/Base", "pages/LoginPage.ts", known))


class Cycles(unittest.TestCase):
    """Two files importing each other have no valid order; say so, convert anyway."""

    def test_a_cycle_becomes_one_final_wave_with_a_note(self):
        selenium = 'import { WebDriver } from "selenium-webdriver";\n'
        with TemporaryDirectory() as tmp:
            root = build(Path(tmp), {
                "pages/A.ts": selenium + 'import { B } from "./B";\nexport const A = B;\n',
                "pages/B.ts": selenium + 'import { A } from "./A";\nexport const B = A;\n',
                "pages/C.ts": selenium + "export const C = 1;\n",
            })
            manifest = suite.scan(root)
        self.assertEqual(manifest.waves, (("pages/C.ts",), ("pages/A.ts", "pages/B.ts")))
        self.assertIn("import cycle between pages/A.ts, pages/B.ts", manifest.notes[0])


class CountingTests(unittest.TestCase):
    """`count_cases` and `census` — the vocabulary the progress line is written in.

    A suite run is minutes long and the page used to describe it as "starting
    the next wave", three times, which is the graph's word for a loop counter
    and nobody else's word for anything. These two functions are what let it say
    "converting 6 page objects" and then "converting 6 test files (8 tests)"
    instead.
    """

    def test_counts_it_and_test_including_modifiers(self):
        source = (
            'describe("Login", () => {\n'
            '  it("logs in", async () => {});\n'
            '  it.only("focuses", async () => {});\n'
            '  it.skip("is pending", async () => {});\n'
            '  test("also a case", async () => {});\n'
            "});\n")
        # Three `it`s (plain, .only, .skip) and one `test`. The `describe` around
        # them is a grouping, not a case, so it is not one of them.
        self.assertEqual(suite.count_cases(source), 4)

    def test_a_method_call_ending_in_it_is_not_a_test(self):
        # `driver.quit()` and `awaitIt(` both contain the letters, and neither is
        # a case. This is the whole job of the lookbehind.
        self.assertEqual(suite.count_cases("await driver.quit();\nawaitIt(x);\n"), 0)
        self.assertEqual(suite.count_cases("suite.it('nope', () => {});\n"), 0)

    def test_a_tagged_template_case_counts(self):
        self.assertEqual(suite.count_cases("test.each`a\\n${1}`('x', () => {});\n"), 1)

    def test_the_sample_suite_is_six_page_objects_and_six_specs(self):
        if not SAMPLE.is_dir():
            self.skipTest("samples are not on this machine")
        manifest = suite.scan(SAMPLE)
        counted = suite.census(manifest.files)
        self.assertEqual(counted["page_objects"], 6)
        self.assertEqual(counted["tests"], 6)
        self.assertEqual(counted["cases"], 8)
        # A page object holds no cases, and is not charged for any.
        self.assertTrue(all(f.cases == 0 for f in manifest.files if f.kind == "page-object"))

    def test_a_census_of_one_wave_is_a_census_of_those_files_only(self):
        with TemporaryDirectory() as folder:
            root = suite.materialize(TOY, Path(folder) / "toy")
            manifest = suite.scan(root)
        by_path = {f.path: f for f in manifest.files}
        first, last = manifest.waves[0], manifest.waves[-1]
        self.assertEqual(suite.census(by_path[p] for p in first)["tests"], 0)
        self.assertEqual(suite.census(by_path[p] for p in last)["tests"], 1)
        self.assertEqual(suite.census(by_path[p] for p in last)["cases"], 1)


class ManifestJson(unittest.TestCase):
    def test_the_payload_is_named_versioned_and_serialisable(self):
        payload = suite.manifest_json(suite.scan(SAMPLE))
        self.assertEqual(payload["schema"], "s2p.suite-manifest/v1")
        self.assertEqual(payload["counts"]["convert"], 12)
        self.assertEqual(len(payload["waves"]), 2)
        first = payload["files"][0]
        self.assertEqual(set(first) - {"path", "kind", "action", "reason", "wave", "lines",
                                       "language", "automation", "runner", "imports",
                                       "imported_by", "external_imports"}, set())
        json.dumps(payload)  # no dataclasses left in it


class ScanCommand(unittest.TestCase):
    """`s2p scan` — the plan on stderr for a person, the JSON on stdout for 9.2."""

    def setUp(self):
        """Pin the console width, so what these tests assert on is the content.

        rich sizes the table to the terminal. At 60 columns the cells fold into
        each other and no amount of string-squeezing puts a path back together
        again — so the width is fixed here rather than inherited from whoever
        happens to be running the tests. How the table looks in a narrow window
        is a rendering question; these tests are about what the command says.
        """
        original = cli.console._width  # None means "ask the terminal"
        cli.console.width = 100
        self.addCleanup(setattr, cli.console, "_width", original)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.run(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_the_table_names_the_waves_and_says_nothing_on_stdout(self):
        code, out, err = self.run_cli("scan", str(SAMPLE))
        self.assertEqual(code, 0)
        self.assertEqual(out, "")
        self.assertIn("12 to convert", err)
        self.assertIn("pages/LoginPage.ts", err)

    def test_json_goes_to_stdout_and_parses(self):
        code, out, err = self.run_cli("scan", str(SAMPLE), "--json")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["schema"], "s2p.suite-manifest/v1")
        self.assertEqual(len(payload["files"]), 12)

    def test_out_writes_the_same_json_to_a_file(self):
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "plan" / "manifest.json"
            code, out, err = self.run_cli("scan", str(SAMPLE), "--out", str(target))
            self.assertEqual(code, 0)
            written = json.loads(target.read_text())
        self.assertEqual(written["counts"]["convert"], 12)
        self.assertIn(f"[wrote {target}]", err)

    def test_a_folder_with_nothing_to_convert_exits_one(self):
        with TemporaryDirectory() as tmp:
            build(Path(tmp), {"tests/smoke.spec.ts": TOY["tests/smoke.spec.ts"]})
            code, out, err = self.run_cli("scan", tmp)
        self.assertEqual(code, 1)
        self.assertIn("Nothing to convert", err)
        self.assertIn("already a Playwright file", err)  # a say() line: soft-wrapped, never folded

    def test_a_missing_folder_is_a_usage_error(self):
        code, out, err = self.run_cli("scan", "/no/such/suite")
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
