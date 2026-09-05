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
        self.assertEqual(finding.line, 33)  # points into the original Selenium file

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


if __name__ == "__main__":
    unittest.main()
