"""Step 9.1 — the suite scanner: what is in the folder, and in what order.

Nothing here calls a model or reaches the network. The scanner is a pure
function of bytes on disk, so every test either points it at the real sample
suite or builds a toy one in a temp directory and reads the plan back.
"""

import io
import os
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

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

    def test_a_bare_root_relative_import_resolves_like_baseUrl(self):
        """`"baseUrl": "."` is how a real repo writes `tests/pages/login.page`."""
        known = {"tests/pages/login.page.ts", "tests/env/manager.ts",
                 "tests/types/driver.d.ts"}
        resolve = suite.resolve_import
        self.assertEqual(resolve("tests/pages/login.page", "tests/specs/e2e.spec.ts", known),
                         "tests/pages/login.page.ts")
        self.assertEqual(resolve("tests/env/manager", "tests/config/browserConfig.ts", known),
                         "tests/env/manager.ts")
        # A declaration file answers to its name without the `.d`.
        self.assertEqual(resolve("tests/types/driver", "tests/env/manager.ts", known),
                         "tests/types/driver.d.ts")
        # The double slash this repo actually contains.
        self.assertEqual(resolve("tests/pages//login.page", "tests/specs/e2e.spec.ts", known),
                         "tests/pages/login.page.ts")

    def test_a_package_is_not_mistaken_for_a_bare_root_import(self):
        """Only the tree answering to it makes a bare specifier one of ours."""
        known = {"tests/pages/login.page.ts"}
        for specifier in ("chai", "selenium-webdriver/chrome", "node:path",
                          "tests/pages/typo.page"):
            with self.subTest(specifier=specifier):
                self.assertIsNone(suite.resolve_import(specifier, "a.ts", known))


class DataFixtures(unittest.TestCase):
    """JSON the suite imports: carried across, never converted, never a package."""

    def plan(self, tree):
        return suite.scan_sources(tree)

    def test_an_imported_fixture_becomes_an_asset_and_not_an_external_import(self):
        manifest = self.plan({
            "tests/testdata/login.json": '{"username": "u"}\n',
            "tests/specs/login.spec.ts": (
                'import { WebDriver } from "selenium-webdriver";\n'
                'import loginData from "tests/testdata/login.json";\n'
                "export const u = loginData.username;\n"),
        })
        self.assertEqual(manifest.assets, ("tests/testdata/login.json",))
        spec = next(f for f in manifest.files if f.path.endswith("login.spec.ts"))
        self.assertEqual(spec.data_imports, ("tests/testdata/login.json",))
        self.assertNotIn("tests/testdata/login.json", spec.external_imports)
        # It is data, so it is not a file in the plan and not in any wave.
        self.assertNotIn("tests/testdata/login.json", [f.path for f in manifest.files])

    def test_a_fixture_nobody_imports_is_not_carried(self):
        manifest = self.plan({
            "fixtures/unused.json": "{}\n",
            "pages/LoginPage.ts": 'import { By } from "selenium-webdriver";\nvoid By;\n',
        })
        self.assertEqual(manifest.assets, ())

    def test_configuration_json_is_never_a_fixture(self):
        manifest = self.plan({
            "package.json": '{"name": "x"}\n',
            "tsconfig.json": "{}\n",
            "a.ts": ('import { By } from "selenium-webdriver";\n'
                     'import pkg from "package.json";\nvoid By; void pkg;\n'),
        })
        self.assertEqual(manifest.assets, ())


SELENIUM = 'import { WebDriver } from "selenium-webdriver";\n'


class Cycles(unittest.TestCase):
    """Two files importing each other have no valid order; say so, convert anyway.

    And convert everything *behind* them in its proper turn. The first planner
    dumped every file a cycle blocked into one last wave, and on a real
    repository — a `lib/` barrel re-exporting a file that imports the barrel
    back — that was twelve of thirteen files converting blind, in parallel.
    """

    def test_a_cycle_converts_together_in_one_wave_with_a_note(self):
        with TemporaryDirectory() as tmp:
            root = build(Path(tmp), {
                "pages/A.ts": SELENIUM + 'import { B } from "./B";\nexport const A = B;\n',
                "pages/B.ts": SELENIUM + 'import { A } from "./A";\nexport const B = A;\n',
                "pages/C.ts": SELENIUM + "export const C = 1;\n",
            })
            manifest = suite.scan(root)
        # Nothing waits on anything outside the cycle, so all three are wave 1:
        # the cycle is not held back, it is simply converted as one.
        self.assertEqual(manifest.waves, (("pages/A.ts", "pages/B.ts", "pages/C.ts"),))
        self.assertEqual(manifest.notes, (
            "import cycle between pages/A.ts, pages/B.ts — converted together in wave 1, none first",))

    def test_files_behind_a_cycle_still_wait_for_it(self):
        manifest = suite.scan_sources({
            "lib/index.ts": SELENIUM + "export * from './page';\n",
            "lib/page.ts": SELENIUM + "import './index';\nexport class Page {}\n",
            "pages/Home.ts": SELENIUM + "import { Page } from '../lib';\nexport class Home extends Page {}\n",
            "specs/a.spec.ts": SELENIUM + "import { Home } from '../pages/Home';\nit('x', () => new Home());\n",
            "lib/util.ts": SELENIUM + "export const u = 1;\n",
        })
        self.assertEqual(manifest.waves, (
            ("lib/index.ts", "lib/page.ts", "lib/util.ts"),
            ("pages/Home.ts",),
            ("specs/a.spec.ts",),
        ))
        self.assertEqual(len(manifest.notes), 1)
        self.assertIn("lib/index.ts, lib/page.ts", manifest.notes[0])

    def test_two_cycles_are_two_notes_in_path_order(self):
        manifest = suite.scan_sources({
            "b1.ts": SELENIUM + "import './b2';\n", "b2.ts": SELENIUM + "import './b1';\n",
            "a1.ts": SELENIUM + "import './a2';\n", "a2.ts": SELENIUM + "import './a1';\n",
        })
        self.assertEqual([n.split(" — ")[0] for n in manifest.notes],
                         ["import cycle between a1.ts, a2.ts", "import cycle between b1.ts, b2.ts"])


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
                                       "language", "automation", "runner", "via", "imports",
                                       "imported_by", "external_imports", "data_imports"}, set())
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


# A bigger toy than TOY: five page objects and four specs, so anything that
# quietly converted only a slice of it would be visible here.
BIG = {}
for _n in range(5):
    BIG[f"pages/Page{_n}.ts"] = (
        'import { WebDriver } from "selenium-webdriver";\n'
        f"export class Page{_n} {{ constructor(protected driver: WebDriver) {{}} }}\n")
for _n in range(4):
    BIG[f"tests/spec{_n}.spec.ts"] = (
        'import { Builder } from "selenium-webdriver";\n'
        f'import {{ Page{_n} }} from "../pages/Page{_n}";\n'
        f'describe("s{_n}", () => {{ it("works", async () => {{ new Page{_n}(null as any); }}); }});\n')


class WholeSuiteOnlyTests(unittest.TestCase):
    """A folder is planned whole. Nothing here converts part of a suite.

    There used to be a per-kind cap that converted three page objects and three
    tests and copied the rest across unchanged. It was a bad trade twice over:
    the slice it chose was the leaves of the wave plan — on one real repo that
    was every driver factory and not a single test file — and a half-converted
    folder is not a result anybody can use. The size decision belongs to the
    meter instead, which is charged for the whole suite before any model runs
    and refuses the run outright when it does not fit.
    """

    def test_a_big_suite_is_planned_whole(self):
        manifest = suite.scan_sources(dict(BIG))
        self.assertEqual(len(manifest.convertible), 9)
        self.assertEqual(manifest.notes, ())
        self.assertEqual([f for f in manifest.files if f.action == suite.COPY], [])

    def test_the_retired_cap_variables_no_longer_truncate_anything(self):
        """A deployment still carrying them must not silently convert a slice."""
        with patch.dict(os.environ, {"S2P_SUITE_MAX_TESTS": "3",
                                     "S2P_SUITE_MAX_PAGE_OBJECTS": "3"}, clear=False):
            manifest = suite.scan_sources(dict(BIG))
        self.assertEqual(len(manifest.convertible), 9)

    def test_the_meter_is_charged_for_every_convertible_file(self):
        """`conversions` is what the guard bills, and it bills the whole folder."""
        self.assertEqual(suite.conversions(dict(BIG)), 9)
        self.assertEqual(suite.conversions(dict(TOY)), 3)

    def test_only_still_narrows_what_a_person_asked_for(self):
        """An explicit filter is a choice, not the tool deciding for them."""
        self.assertEqual(suite.conversions(dict(BIG), ["pages/*.ts"]), 5)



if __name__ == "__main__":
    unittest.main()


# A repository that wraps WebDriver in its own `lib/`, the shape of
# goenning/typescript-selenium-example: one file imports Selenium, the barrel
# re-exports it, the page object imports the barrel, the spec imports the page
# object. Read one file at a time, only the first is Selenium.
WRAPPED = {
    "lib/driver.ts": (
        'import { Builder, WebDriver } from "selenium-webdriver";\n'
        "export class Browser { driver: WebDriver = new Builder().build(); }\n"),
    "lib/conditions.ts": (
        'import { Browser } from "./driver";\n'
        "export const visible = (b: Browser) => true;\n"),
    "lib/index.ts": "export * from './driver';\nexport * from './conditions';\n",
    "lib/bdd.ts": "export const when = (name: string, fn: () => void) => describe(name, fn);\n",
    "pages/HomePage.ts": (
        "import { Browser } from '../lib';\n"
        "export class HomePage { constructor(public browser: Browser) {} }\n"),
    "specs/home.spec.ts": (
        "import { HomePage } from '../pages/HomePage';\n"
        "import { when } from '../lib/bdd';\n"
        "when('home', () => { it('loads', () => new HomePage(null as any)); });\n"),
    "config.ts": "export default { baseUrl: 'http://x' };\n",
}


class WrapperSuite(unittest.TestCase):
    """A file that imports a Selenium file is Selenium, and so on up the imports.

    Measured on the real repo this fixture is shaped after: 4 of 16 files import
    selenium-webdriver, 13 of 16 reach it. Read alone, the nine in between were
    "no recognised automation library", so they were copied across and sat in
    the converted tree as Selenium — every page object, the spec, the barrel.
    """

    def setUp(self):
        self.manifest = suite.scan_sources(WRAPPED)
        self.by_path = {f.path: f for f in self.manifest.files}

    def actions(self) -> dict[str, str]:
        return {path: f.action for path, f in self.by_path.items()}

    def test_everything_that_reaches_selenium_converts_and_nothing_else_does(self):
        self.assertEqual(self.actions(), {
            "lib/driver.ts": suite.CONVERT,  # imports it
            "lib/conditions.ts": suite.CONVERT,  # imports the file that does
            "lib/index.ts": suite.CONVERT,  # re-exports both
            "pages/HomePage.ts": suite.CONVERT,  # imports the barrel
            "specs/home.spec.ts": suite.CONVERT,  # imports the page object
            "lib/bdd.ts": suite.COPY,  # a runner helper: imports nothing, reaches nothing
            "config.ts": suite.COPY,
        })
        self.assertEqual(suite.conversions(WRAPPED), 5)

    def test_a_flipped_file_says_which_import_made_it_selenium(self):
        home = self.by_path["pages/HomePage.ts"]
        self.assertEqual(home.kind, "page-object")
        self.assertEqual(home.classification.automation, "selenium")
        self.assertTrue(home.classification.supported)
        self.assertEqual(home.classification.via, "lib/index.ts")
        self.assertEqual(home.reason, "page object / helper driving Selenium through lib/index.ts")
        spec = self.by_path["specs/home.spec.ts"]
        self.assertEqual((spec.kind, spec.classification.via), ("test", "pages/HomePage.ts"))
        self.assertEqual(spec.reason, "mocha test file driving Selenium through pages/HomePage.ts")
        # A direct import keeps the wording it always had, and no `via`.
        driver = self.by_path["lib/driver.ts"]
        self.assertEqual((driver.classification.via, driver.reason),
                         ("", "page object / helper driving Selenium"))

    def test_a_flipped_test_file_has_its_cases_counted(self):
        self.assertEqual(self.by_path["specs/home.spec.ts"].cases, 1)
        self.assertEqual(suite.census(self.manifest.files)["tests"], 1)

    def test_the_waves_run_up_the_import_chain(self):
        wave = {p: f.wave for p, f in self.by_path.items()}
        self.assertLess(wave["lib/driver.ts"], wave["lib/conditions.ts"])
        self.assertLess(wave["lib/conditions.ts"], wave["lib/index.ts"])
        self.assertLess(wave["lib/index.ts"], wave["pages/HomePage.ts"])
        self.assertLess(wave["pages/HomePage.ts"], wave["specs/home.spec.ts"])
        self.assertEqual((wave["lib/bdd.ts"], wave["config.ts"]), (0, 0))

    def test_the_manifest_json_carries_the_path_it_was_reached_through(self):
        rows = {row["path"]: row for row in suite.manifest_json(self.manifest)["files"]}
        self.assertEqual(rows["pages/HomePage.ts"]["via"], "lib/index.ts")
        self.assertEqual(rows["lib/driver.ts"]["via"], "")

    def test_a_recognised_library_is_never_overridden(self):
        """A Playwright spec that imports the Selenium wrapper is still Playwright."""
        tree = WRAPPED | {"specs/new.spec.ts": (
            'import { test } from "@playwright/test";\n'
            "import { HomePage } from '../pages/HomePage';\n"
            "test('x', () => new HomePage(null as any));\n")}
        item = {f.path: f for f in suite.scan_sources(tree).files}["specs/new.spec.ts"]
        self.assertEqual(item.action, suite.SKIP)
        self.assertIn("already a Playwright file", item.reason)
        self.assertEqual(item.classification.via, "")

    def test_a_javascript_file_reaching_selenium_is_refused_like_a_direct_one(self):
        """Same answer as a `.js` file that imports selenium-webdriver itself: not v1."""
        tree = WRAPPED | {"legacy/old.js": "const { HomePage } = require('../pages/HomePage');\n"}
        item = {f.path: f for f in suite.scan_sources(tree).files}["legacy/old.js"]
        self.assertEqual(item.action, suite.SKIP)
        self.assertEqual(item.classification.automation, "selenium")
        self.assertIn("javascript source reaching Selenium through pages/HomePage.ts", item.reason)
        self.assertIn("v1 converts TypeScript only", item.reason)

    def test_a_cycle_of_helpers_that_never_reaches_selenium_terminates_and_stays_copied(self):
        tree = {"a.ts": "import './b';\nexport const a = 1;\n",
                "b.ts": "import './a';\nexport const b = 2;\n"}
        manifest = suite.scan_sources(tree)
        self.assertEqual({f.action for f in manifest.files}, {suite.COPY})

    def test_the_sample_suite_is_unchanged(self):
        """Every file there imports Selenium directly, so nothing has anything to flip."""
        manifest = suite.scan(SAMPLE)
        self.assertEqual({f.classification.via for f in manifest.files}, {""})
        self.assertEqual(len(manifest.convertible), 12)
