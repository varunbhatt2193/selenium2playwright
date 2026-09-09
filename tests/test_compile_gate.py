"""Gate 1's tsconfig: the aliases a real repository imports by.

A suite that writes `@lib/x` instead of `../lib/x` used to fail every file on
TS2307, because the alias is defined in the project's own tsconfig and the
sandbox compiles against a frozen one. `alias_paths` derives them from the tree
itself. Most of these tests are pure — only one pays for a real `tsc` run.
"""

import unittest

from selenium2playwright.schemas import Finding
from selenium2playwright.validators.compile import (alias_paths, compile_check,
                                                    missing_dependency)
from selenium2playwright.validators.residue import residue_check

# The shape that failed on the live demo: three aliases, one real npm scope.
ALIASED = {
    "lib/browser.types.ts": "export type BrowserName = 'chrome' | 'firefox';\n",
    "lib/test.metadata.ts": ('import { BrowserName } from "@lib/browser.types";\n'
                             "export interface TestMetadata { browser: BrowserName }\n"),
    "pages/index.ts": "export class Pages { name = 'p'; }\n",
    "config/env.ts": "export default { db: 'x' };\n",
    "lib/test.context.ts": ('import { Pages } from "@pages/index";\n'
                            'import { TestMetadata } from "@lib/test.metadata";\n'
                            'import env from "@config/env";\n'
                            "export class TestContext {\n"
                            "  constructor(public pages: Pages, public meta: TestMetadata) {}\n"
                            "  db() { return env.db; }\n"
                            "}\n"),
    "tests/a.spec.ts": ('import { test } from "@playwright/test";\n'
                        'import { TestContext } from "@lib/test.context";\n'
                        "test('t', async () => { void TestContext; });\n"),
}


class AliasDerivationTests(unittest.TestCase):
    """Pure: what `paths` does this tree need, and what must never be in them."""

    def test_an_alias_pointing_into_the_tree_is_mapped(self):
        self.assertEqual(alias_paths(ALIASED), {
            "@config/*": ["config/*"], "@lib/*": ["lib/*"], "@pages/*": ["pages/*"]})

    def test_a_real_npm_scope_is_left_alone(self):
        """`@playwright/test` must resolve from node_modules, not from the folder."""
        self.assertNotIn("@playwright/*", alias_paths(ALIASED))

    def test_a_tree_with_no_aliases_asks_for_nothing(self):
        plain = {"pages/LoginPage.ts": "export class L {}\n",
                 "tests/a.spec.ts": 'import { L } from "../pages/LoginPage";\nvoid L;\n'}
        self.assertEqual(alias_paths(plain), {})

    def test_a_root_alias_maps_to_the_root(self):
        for prefix in ("@", "~"):
            with self.subTest(prefix=prefix):
                tree = {"lib/x.ts": "export const x = 1;\n",
                        "a.ts": f'import {{ x }} from "{prefix}/lib/x";\nvoid x;\n'}
                self.assertEqual(alias_paths(tree), {f"{prefix}/*": ["*"]})

    def test_an_alias_onto_a_directory_counts(self):
        """`@lib/index` need not exist as a file for `@lib` to be an alias."""
        tree = {"lib/thing.ts": "export const a = 1;\n",
                "b.ts": 'import { a } from "@lib/thing";\nvoid a;\n'}
        self.assertEqual(alias_paths(tree), {"@lib/*": ["lib/*"]})

    def test_a_bare_scope_with_no_path_is_not_an_alias(self):
        tree = {"a.ts": 'import x from "@scope";\nvoid x;\n'}
        self.assertEqual(alias_paths(tree), {})


class AliasedTreeCompilesTests(unittest.TestCase):
    """One real `tsc` run: the whole point, end to end."""

    def test_a_suite_that_imports_by_alias_compiles(self):
        report = compile_check(ALIASED)
        self.assertTrue(report.passed,
                        "aliased imports should resolve: "
                        + "; ".join(f"{f.file}:{f.line} {f.code} {f.message}"
                                   for f in report.findings))


class RunnerGlobalTests(unittest.TestCase):
    """`Cannot find name 'context'` is a missing @types/mocha, not a bad conversion."""

    def finding(self, code, message):
        return Finding(gate="compile", file="tests/a.test.ts", line=1, column=1,
                       code=code, message=message)

    def test_a_mocha_global_is_an_absent_dependency(self):
        self.assertTrue(missing_dependency(
            self.finding("TS2304", "Cannot find name 'context'."), set()))

    def test_a_playwright_name_is_not_excused(self):
        """`expect` ships with @playwright/test, which IS installed — a missing
        import there is a real bug and must keep failing the gate."""
        for name in ("expect", "test"):
            with self.subTest(name=name):
                self.assertFalse(missing_dependency(
                    self.finding("TS2304", f"Cannot find name '{name}'."), set()))

    def test_an_ordinary_undefined_name_is_not_excused(self):
        self.assertFalse(missing_dependency(
            self.finding("TS2304", "Cannot find name 'loginPge'."), set()))

    def test_residue_still_refuses_every_global_compile_now_excuses(self):
        """The safety interlock: compile may only excuse what residue catches."""
        from selenium2playwright.validators.compile import RUNNER_GLOBALS
        for name in sorted(RUNNER_GLOBALS - {"jest", "cy", "chai"}):
            with self.subTest(name=name):
                report = residue_check({"out.ts": f"{name}('x', () => {{}});\n"})
                self.assertFalse(report.passed, f"residue lets {name}( through")


class SeleniumStillCaughtTests(unittest.TestCase):
    """The compile gate now tolerates absent packages. Selenium is one of them."""

    LEFTOVER = {"pages/Half.ts": ('import { By } from "selenium-webdriver";\n'
                                  "export const locator = By.css('#a');\n")}

    def test_compile_no_longer_fails_on_the_missing_selenium_package(self):
        """Stated so the next reader knows it is deliberate, not a regression."""
        self.assertTrue(compile_check(self.LEFTOVER).passed)

    def test_but_the_residue_gate_still_refuses_it(self):
        """Which is the gate that was always meant to own this, and still does."""
        report = residue_check(self.LEFTOVER)
        self.assertFalse(report.passed)
        self.assertTrue(any("selenium" in (f.message or "").lower() for f in report.findings),
                        [f.message for f in report.findings])


if __name__ == "__main__":
    unittest.main()
