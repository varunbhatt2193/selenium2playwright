"""Offline regressions for the parity gate; no model or browser calls.

Run: .venv/bin/python -m unittest discover -s tests -v
"""

import unittest
from pathlib import Path

from selenium2playwright.validators.parity import parity_check

ROOT = Path(__file__).resolve().parents[1]


def compare(source: str, converted: str):
    return parity_check({"case.spec.ts": source}, {"case.spec.ts": converted})


class ParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        def read_tree(folder):
            base = ROOT / "samples" / folder
            return {str(p.relative_to(base)): p.read_text() for p in base.rglob("*.ts")}
        cls.source = read_tree("selenium-suite")
        cls.golden = read_tree("playwright-golden")

    def test_golden_suite_and_pom_pass(self):
        report = parity_check(self.source, self.golden)
        self.assertTrue(report.passed, report.render())
        self.assertEqual(report.findings, [])

    def test_deleted_golden_assertion_names_affected_test(self):
        files = dict(self.golden)
        files["tests/login.spec.ts"] = files["tests/login.spec.ts"].replace(
            '    await expect(loginPage.flashMessage).toContainText(\n'
            '      "Your password is invalid!"\n    );',
            '// expect(loginPage.flashMessage).toContainText("Your password is invalid!");',
        )
        report = parity_check(self.source, files)
        self.assertFalse(report.passed)
        self.assertEqual(len(report.findings), 1)
        finding = report.findings[0]
        self.assertEqual(finding.code, "missing-assertion")
        self.assertIn("rejects invalid credentials", finding.message)
        self.assertIn('expect(flash).to.contain("Your password is invalid!")', finding.message)
        self.assertEqual(finding.file, "tests/login.spec.ts")
        # Match the actual source location even when browser setup gains comments.
        source_lines = self.source["tests/login.spec.ts"].splitlines()
        expected_line = next(i for i, line in enumerate(source_lines, 1)
                             if 'expect(flash).to.contain("Your password is invalid!")' in line)
        self.assertEqual(finding.line, expected_line)

    def test_renamed_test_does_not_hide_behind_equal_totals(self):
        report = compare('it("original", () => { expect(x).to.equal(1); });',
                         'test("renamed", () => { expect(x).toBe(1); });')
        self.assertFalse(report.passed)
        self.assertEqual(report.findings[0].code, "missing-test")
        self.assertIn("original", report.findings[0].message)

    def test_assertions_cannot_move_to_another_test_to_hide_loss(self):
        report = compare('it("a", () => { assert.equal(x, 1); }); it("b", () => { assert(x); });',
                         'test("a", () => {}); test("b", () => { expect(x).toBe(1); expect(x).toBeTruthy(); });')
        self.assertFalse(report.passed)
        self.assertEqual(len(report.findings), 1)
        self.assertIn("test 'a'", report.findings[0].message)

    def test_duplicate_titles_and_suite_identity_are_preserved(self):
        cases = [
            ('it("same", () => {}); it("same", () => {});', 'test("same", () => {});'),
            ('describe("a", () => { it("same", () => {}); });',
             'test.describe("b", () => { test("same", () => {}); });'),
        ]
        for source, converted in cases:
            with self.subTest(source=source):
                report = compare(source, converted)
                self.assertFalse(report.passed)
                self.assertEqual(report.findings[0].code, "missing-test")

    def test_nested_callbacks_multiline_chains_and_import_aliases(self):
        source = '''import { it as scenario } from "mocha";
          import { assert as check, expect as verify } from "chai";
          scenario("dialog", () => { check.equal(message, "ok"); verify(x).to.be.true; });'''
        converted = '''import { test as scenario, expect as check } from "@playwright/test";
          scenario("dialog", async ({page}) => {
            page.once("dialog", d => { check(d.message()).toBe("ok"); });
            await check.soft(page.locator("button"))
              .toBeVisible();
          });'''
        report = compare(source, converted)
        self.assertTrue(report.passed, report.render())

    def test_comments_strings_and_regex_literals_cannot_replace_assertions(self):
        source = 'it("kept", () => { assert(x); });'
        converted = '''test("kept", () => {
          /* expect(x).toBeTruthy(); */
          // expect(x).toBeTruthy();
          const text = "expect(x).toBeTruthy(); it('fake', () => {});";
          const pattern = /expect(x)/;
        });'''
        report = compare(source, converted)
        self.assertFalse(report.passed)
        self.assertEqual([f.code for f in report.findings], ["missing-assertion"])

    def test_missing_file_is_reported_even_for_pom_without_assertions(self):
        report = parity_check({"Page.ts": "export class Page {}"}, {})
        self.assertEqual(report.findings[0].code, "missing-file")
        self.assertFalse(report.passed)

    def test_namespace_imports_preserve_assertion_counts(self):
        source = 'import * as chai from "chai"; it("a", () => { chai.assert.equal(x, 1); });'
        converted = 'import * as pw from "@playwright/test"; pw.test("a", () => { pw.expect(x).toBe(1); });'
        self.assertTrue(compare(source, converted).passed)
        self.assertFalse(compare(source, converted.replace('pw.expect(x).toBe(1);', '')).passed)

    def test_new_skip_or_skipped_suite_fails_but_existing_skip_passes(self):
        for converted in ('test.skip("a", () => {});', 'test("a");'):
            self.assertEqual(compare('it("a", () => {});', converted).findings[0].code, "disabled-test")
        report = compare('describe("s", () => { it("a", () => {}); });',
                         'test.describe.skip("s", () => { test("a", () => {}); });')
        self.assertEqual(report.findings[0].code, "disabled-test")
        self.assertTrue(compare('xit("a", () => {});', 'test.skip("a", () => {});').passed)

    def test_outside_assertions_are_checked_and_bare_expect_does_not_count(self):
        report = compare('beforeEach(() => { assert(x); });', 'test.beforeEach(() => {});')
        self.assertEqual(report.findings[0].code, "missing-assertion")
        report = compare('it("a", () => { expect(x).to.equal(1); });', 'test("a", () => { expect(x); });')
        self.assertEqual(report.findings[0].code, "missing-assertion")

    def test_unverifiable_shapes_fail_explicitly(self):
        for code in ('it(`case ${id}`, () => {});', 'it.each([1, 2])("case", () => {});',
                     'test("case", handler);', 'const broken = ;',
                     'test("case", () => { test.skip(condition, "reason"); });'):
            with self.subTest(code=code):
                report = compare(code, code)
                self.assertFalse(report.passed)
                self.assertTrue(all(f.code == "unverified-parity" for f in report.findings))


GAINS = {"new-import", "dynamic-load", "code-from-string"}


def gains(source: str, converted: str, tree=("case.spec.ts",)):
    report = parity_check({"case.spec.ts": source}, {"case.spec.ts": converted}, tree=tree)
    return [f.code for f in report.findings if f.code in GAINS]


class GainedLoadTests(unittest.TestCase):
    """The injection half: a conversion may not load what its source never did.

    A comment in somebody else's Selenium file can ask the model to add
    `child_process`. That compiles, is not residue, lints clean and drops no
    assertion, so this is the only gate that can refuse it.
    """

    SOURCE = 'import { By } from "selenium-webdriver";\nimport "dotenv/config";\nimport fs from "fs";\n'

    def test_the_injected_import_fails_and_names_the_module(self):
        report = parity_check({"case.spec.ts": self.SOURCE}, {"case.spec.ts": (
            'import { test } from "@playwright/test";\n'
            'import { execSync } from "child_process";\n')}, tree=["case.spec.ts"])
        self.assertFalse(report.passed)
        self.assertEqual([f.code for f in report.findings], ["new-import"])
        self.assertEqual(report.findings[0].line, 2)
        self.assertIn("child_process", report.findings[0].message)

    def test_every_way_of_loading_a_module_is_seen(self):
        for code in ('import cp from "child_process";', 'export * from "child_process";',
                     'import cp = require("child_process");', 'const cp = require("child_process");',
                     'const cp = await import("child_process");', 'import "child_process";'):
            with self.subTest(code=code):
                self.assertEqual(gains(self.SOURCE, code), ["new-import"])

    def test_text_that_hides_code_from_a_line_scan_does_not_hide_it_here(self):
        # A `//` inside a string, and a regex literal holding `/*`, both look
        # like comments to a line-based scan and would blank the real import.
        for code in ('await page.goto("https://a.test"); await import("child_process");',
                     'const r = /a\\/*/; await import("child_process"); // */'):
            with self.subTest(code=code):
                self.assertEqual(gains(self.SOURCE, code), ["new-import"])

    def test_loads_nobody_can_read_and_code_from_strings_fail(self):
        cases = {
            'const m = "child_" + "process"; await import(m);': "dynamic-load",
            'const r = require; r("child_process");': "dynamic-load",
            'eval("1");': "code-from-string",
            'new Function("return 1")();': "code-from-string",
            'globalThis["ev" + "al"]("1");': "code-from-string",
            'module.require("child_process");': "code-from-string",
            '(async () => {}).constructor("return 1")();': "code-from-string",
        }
        for code, expected in cases.items():
            with self.subTest(code=code):
                self.assertIn(expected, gains(self.SOURCE, code))

    def test_what_the_source_already_had_is_not_a_gain(self):
        for code in ('import { test } from "@playwright/test";', 'import "dotenv/config";',
                     'import fs from "node:fs";', 'import { readFileSync } from "fs";',
                     'import type { ChildProcess } from "child_process";',
                     'import { type ChildProcess } from "child_process";',
                     'import { LoginPage } from "../pages/LoginPage";',
                     'page.evaluate(() => window.scrollTo(0, 0));', 'const t = page.evaluate;'):
            with self.subTest(code=code):
                self.assertEqual(gains(self.SOURCE, code), [])
        dynamic = 'const f = "a"; require(f);\n'
        self.assertEqual(gains(dynamic, "const f = 'a'; await import(f);"), [])
        self.assertEqual(gains('eval("x");', 'eval("x");'), [])

    def test_the_suites_own_files_are_not_new_packages(self):
        tree = ["tests/login.spec.ts", "pages/admin/login.page.ts", "tests/pages/cart.page.ts"]
        self.assertEqual(gains("", 'import { L } from "@pages/admin/login.page";', tree), [])
        self.assertEqual(gains("", 'import { C } from "tests/pages/cart.page";', tree), [])
        # A scope the source already reached into is the suite's own alias.
        self.assertEqual(gains('import { a } from "@lib/a";', 'import { b } from "@lib/b";'), [])

    def test_a_file_in_the_suite_cannot_vouch_for_a_same_named_builtin(self):
        self.assertEqual(gains("", 'import fs from "fs";', ["utils/fs.ts", "fs/index.d.ts"]), ["new-import"])
        self.assertEqual(gains("", 'import fs from "node:fs";', ["fs.ts"]), ["new-import"])

    def test_without_a_tree_the_gate_is_exactly_what_it_was(self):
        """The evaluators call it this way, and their stored scores must not move."""
        report = compare(self.SOURCE, 'import { execSync } from "child_process";\neval("1");\n')
        self.assertTrue(report.passed, report.render())
        self.assertNotIn('"loads"', report.tool_output)


if __name__ == "__main__":
    unittest.main()
