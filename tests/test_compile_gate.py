"""Gate 1's tsconfig: the aliases a real repository imports by.

A suite that writes `@lib/x` instead of `../lib/x` used to fail every file on
TS2307, because the alias is defined in the project's own tsconfig and the
sandbox compiles against a frozen one. `alias_paths` derives them from the tree
itself. Most of these tests are pure — only one pays for a real `tsc` run.
"""

import unittest

from selenium2playwright.schemas import Finding
from selenium2playwright.validators.compile import (alias_paths, compile_check,
                                                    missing_dependency, root_imports)
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


# The shape sadabnepal/selenium-javascript-test actually has: `"baseUrl": "."`
# in its own tsconfig, every import written from the project root, and the
# fixtures in JSON.
ROOTED = {
    "tests/testdata/login.json": '{"username": "standard_user", "password": "secret"}\n',
    "tests/pages/login.page.ts": (
        "import { Page } from '@playwright/test';\n"
        "export class LoginPage {\n"
        "  constructor(private page: Page) {}\n"
        "  async login(user: string, pass: string) { void user; void pass; }\n}\n"),
    "tests/specs/login.spec.ts": (
        "import { test } from '@playwright/test';\n"
        "import { LoginPage } from 'tests/pages/login.page';\n"
        "import loginData from 'tests/testdata/login.json';\n"
        "test('login', async ({ page }) => {\n"
        "  await new LoginPage(page).login(loginData.username, loginData.password);\n});\n"),
}


class RootImportTests(unittest.TestCase):
    """Pure: does this tree resolve anything against its own root?"""

    def test_a_tree_that_imports_itself_by_root_path_is_recognised(self):
        self.assertTrue(root_imports(ROOTED))

    def test_a_relative_tree_asks_for_nothing(self):
        plain = {"pages/LoginPage.ts": "export class L {}\n",
                 "tests/a.spec.ts": 'import { L } from "../pages/LoginPage";\nvoid L;\n'}
        self.assertFalse(root_imports(plain))

    def test_packages_alone_do_not_turn_baseUrl_on(self):
        """`chai` and `node:os` resolve to nothing here, so they are packages."""
        tree = {"a.ts": ('import { expect } from "chai";\n'
                         'import os from "node:os";\nvoid expect; void os;\n')}
        self.assertFalse(root_imports(tree))


class RootedTreeCompilesTests(unittest.TestCase):
    """Pays for a real `tsc` run: the baseUrl + JSON shape has to actually pass."""

    def test_root_relative_imports_and_a_json_fixture_compile(self):
        report = compile_check(ROOTED)
        self.assertTrue(report.passed, report.render())
        self.assertEqual(report.findings, [])
        self.assertEqual(report.excused, [])

    def test_a_misspelt_root_import_is_still_a_real_finding(self):
        """The whole risk of turning baseUrl on: it must not swallow a typo."""
        broken = dict(ROOTED)
        broken["tests/specs/login.spec.ts"] = broken["tests/specs/login.spec.ts"].replace(
            "tests/pages/login.page", "tests/pages/typo.page")
        report = compile_check(broken)
        self.assertFalse(report.passed)
        self.assertEqual(report.excused, [])
        self.assertIn("typo.page", report.render())


class BareSpecifierExcusalTests(unittest.TestCase):
    """Which unresolved bare import is this suite's fault, and which is not."""

    tree = {"tests/pages/login.page.ts": "export class L {}\n"}

    def excused(self, specifier):
        finding = Finding(gate="compile", file="tests/pages/login.page.ts", line=1,
                          column=1, severity="error", code="TS2307",
                          message=f"Cannot find module '{specifier}' or its "
                                  "corresponding type declarations.")
        return missing_dependency(finding, set(self.tree))

    def test_an_uninstalled_package_is_excused(self):
        for specifier in ("chai", "zod", "selenium-webdriver/chrome", "dotenv"):
            with self.subTest(specifier=specifier):
                self.assertTrue(self.excused(specifier))

    def test_a_broken_import_into_this_tree_is_not(self):
        """`tests/` is a folder right here, so this is a typo, not a dependency."""
        self.assertFalse(self.excused("tests/pages/typo.page"))


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

    def test_the_same_global_is_excused_under_its_other_error_code(self):
        """tsc renumbers to TS2582 when it can name the @types package."""
        finding = Finding(gate="compile", file="a.ts", code="TS2582",
                          message="Cannot find name 'describe'. Do you need to install type "
                                  "definitions for a test runner? Try `npm i --save-dev "
                                  "@types/jest` or `npm i --save-dev @types/mocha`.")
        self.assertTrue(missing_dependency(finding, {"a.ts"}))

    def test_a_playwright_name_is_not_excused_under_either_code(self):
        for code in ("TS2304", "TS2582"):
            with self.subTest(code=code):
                finding = Finding(gate="compile", file="a.ts", code=code,
                                  message="Cannot find name 'expect'.")
                self.assertFalse(missing_dependency(finding, {"a.ts"}))

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


class ExcusedFindingTests(unittest.TestCase):
    """A passing report must not read like a failing one.

    The gate stopped counting absent packages against the file, but it kept
    listing them under `PASS compile: passed` — and the critic, told a failed
    gate requires revise, quoted the contradiction back and voted revise on
    three of three clean files in a live run. What the gate excused now lives
    in `excused`, which `render()` does not print.
    """

    LEFTOVER = {"pages/Half.ts": ('import { By } from "selenium-webdriver";\n'
                                  "export const locator = By.css('#a');\n")}

    def setUp(self):
        self.report = compile_check(self.LEFTOVER)

    def test_the_absent_package_is_excused_not_blocking(self):
        self.assertTrue(self.report.passed)
        self.assertEqual([], self.report.findings, [f.render() for f in self.report.findings])
        self.assertTrue(self.report.excused, "the error tsc printed was thrown away")
        self.assertTrue(any("selenium-webdriver" in f.message for f in self.report.excused))

    def test_render_gives_the_critic_nothing_it_cannot_fix(self):
        rendered = self.report.render()
        self.assertIn("compile: passed", rendered)
        self.assertNotIn("selenium-webdriver", rendered)
        self.assertNotIn("TS2307", rendered)

    def test_a_real_error_is_still_blocking_and_still_rendered(self):
        report = compile_check({"pages/Broken.ts": "export const n: number = 'text';\n"})
        self.assertFalse(report.passed)
        self.assertTrue(report.findings)
        self.assertIn("TS2322", report.render())


class CarriedCompanionTests(unittest.TestCase):
    """An error inside a file the run never converted is not this file's fault.

    A suite hands `tsc` the companions so imports resolve. In a real repository
    most of those companions are still Selenium — they are copied across
    untouched, and Selenium does not compile in this sandbox by design. Before
    this, every one of their errors failed the target's compile gate.

    Measured on goenning/typescript-selenium-example, live: all four converted
    files scored `needs-review` on every lap. The critic diagnosed it correctly
    and could do nothing — the errors were in files it had been told not to
    touch — so it spent three attempts each asking us to rerun the validator
    "with the intended target scope". This is that scope.
    """

    # `lib/index.ts` is the shape that did it: a barrel of Selenium the
    # converted file imports, which cannot type-check without the package.
    TREE = {
        "lib/index.ts": ("import { WebDriver } from 'selenium-webdriver';\n"
                         "export const stale: WebDriver = undefined as never;\n"
                         "export const n: number = 'not a number';\n"),
        "pages/Login.ts": ("import { Page } from '@playwright/test';\n"
                           "export class LoginPage {\n"
                           "  constructor(private page: Page) {}\n"
                           "  async open() { await this.page.goto('/login'); }\n"
                           "}\n"),
    }

    def test_a_carried_companions_own_error_fails_the_gate_when_nobody_says_it_is_carried(self):
        """The bug, pinned: without `carried`, the neighbour's error is ours."""
        report = compile_check(self.TREE)
        self.assertFalse(report.passed)
        self.assertTrue(any(f.file == "lib/index.ts" for f in report.findings))

    def test_naming_it_carried_excuses_it(self):
        report = compile_check(self.TREE, others={"lib/index.ts"})
        self.assertTrue(report.passed, report.render())
        self.assertEqual([], report.findings)
        self.assertTrue(any(f.file == "lib/index.ts" for f in report.excused))

    def test_the_critic_is_not_shown_work_it_cannot_do(self):
        rendered = compile_check(self.TREE, others={"lib/index.ts"}).render()
        self.assertIn("compile: passed", rendered)
        self.assertNotIn("lib/index.ts", rendered)

    def test_the_converted_file_is_never_excused_by_its_neighbours(self):
        """Carrying a companion must not turn the gate off for the target."""
        broken = self.TREE | {"pages/Login.ts": "export const n: number = 'text';\n"}
        report = compile_check(broken, others={"lib/index.ts"})
        self.assertFalse(report.passed)
        self.assertEqual(["pages/Login.ts"], [f.file for f in report.findings])

    def test_a_companion_that_was_converted_still_answers_for_itself(self):
        """Only *carried* companions are excused — a converted one is output."""
        report = compile_check(self.TREE, others=set())
        self.assertFalse(report.passed)
        self.assertTrue(any(f.file == "lib/index.ts" for f in report.findings))

    def test_the_whole_tree_compile_excuses_nothing_by_default(self):
        """`assemble` reports on the final tree, where no file is a companion."""
        self.assertFalse(compile_check(self.TREE).passed)


if __name__ == "__main__":
    unittest.main()
