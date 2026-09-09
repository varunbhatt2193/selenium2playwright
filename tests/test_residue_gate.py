"""Gate 2: what counts as Selenium surviving a conversion, and what does not.

The gate is regex over plain lines, which is what makes it independent of both
the compiler and the model — and also what makes it able to be wrong in one
specific way: a *name* can look like an API. `driver.` used to be matched on
its own, and on a live run it failed a correctly converted Playwright helper
whose parameter had kept the old name, burning a repair lap on a rename. These
tests pin both halves: the rule still refuses every Selenium call it ever did,
and it no longer has an opinion about what anybody's variables are called.
"""

import unittest

from selenium2playwright.validators.residue import residue_check


class SeleniumApiStillCaughtTests(unittest.TestCase):
    """Every shape of surviving Selenium the gate is the last defence against.

    The compile gate deliberately excuses `selenium-webdriver` as a package the
    sandbox never installs, so nothing else in the pipeline fails these lines.
    """

    LEFTOVERS = {
        "a navigation": "await driver.get(url);\n",
        "a session teardown": "await driver.quit();\n",
        "a teardown on any receiver": "await session.quit();\n",
        "an element lookup": 'const el = await driver.findElement(By.id("x"));\n',
        "an element lookup on any receiver": 'await page.findElement(By.id("x"));\n',
        "the options tree": "await driver.manage().window().maximize();\n",
        "an explicit sleep": "await driver.sleep(1000);\n",
        "an explicit wait": "await driver.wait(until.elementLocated(By.css('.a')), 5000);\n",
        "a title read": "const t = await driver.getTitle();\n",
        "a url read on any receiver": "const u = await session.getCurrentUrl();\n",
        "a page-source read": "const html = await session.getPageSource();\n",
        "a script escape hatch": 'await driver.executeScript("return 1");\n',
        "a frame switch": "await driver.switchTo().frame(0);\n",
        "history navigation": "await driver.navigate().back();\n",
        "a screenshot": "await driver.takeScreenshot();\n",
        "window handles": "const hs = await session.getWindowHandles();\n",
        "the type": "const d: WebDriver = build();\n",
        "the element type": "let el: WebElement;\n",
        "the builder": "const d = new Builder().forBrowser('chrome').build();\n",
        "a locator helper": "const by = By.css('.a');\n",
        "a keystroke": 'await field.sendKeys("hunter2");\n',
    }

    def test_every_selenium_shape_fails_the_gate(self):
        for what, line in self.LEFTOVERS.items():
            with self.subTest(what=what):
                report = residue_check({"out.ts": line})
                self.assertFalse(report.passed, f"{what} sailed through: {line!r}")

    def test_the_import_is_refused_on_its_own(self):
        report = residue_check(
            {"out.ts": 'import { By } from "selenium-webdriver";\nexport const a = 1;\n'})
        self.assertFalse(report.passed)


class AVariableNamedDriverIsNotSeleniumTests(unittest.TestCase):
    """The false positive this rule was narrowed for, kept from coming back.

    `driver` is what a Selenium suite calls the thing it passes around, so a
    conversion that keeps the name — a session object, a fixture, a parameter —
    is a normal and often *good* outcome: it keeps call sites recognisable. It
    is not residue, and failing it sends the repair loop to rewrite code that
    was already right.
    """

    # Reduced from tests/config/driverFactory.ts on a live run of
    # sadabnepal/selenium-javascript-test, where this failed the residue gate
    # on lap 1 with four findings and cost the file one of its three attempts.
    HELPER = {"config/driverFactory.ts": (
        "import { Browser, BrowserContext, Page } from 'playwright';\n"
        "export interface Session { browser: Browser; context: BrowserContext; page: Page }\n"
        "export async function quiteDriver(driver: Session | Browser): Promise<void> {\n"
        "  if ('browser' in driver) {\n"
        "    await driver.page.close();\n"
        "    await driver.context.close();\n"
        "    await driver.browser.close();\n"
        "  } else {\n"
        "    await driver.close();\n"
        "  }\n"
        "}\n")}

    def test_playwright_reached_through_a_variable_called_driver_passes(self):
        report = residue_check(self.HELPER)
        self.assertTrue(report.passed, report.render())

    def test_the_same_helper_fails_the_moment_it_is_actually_selenium(self):
        """The narrowing must not have turned the rule off for this file."""
        selenium = {"config/driverFactory.ts":
                    self.HELPER["config/driverFactory.ts"] + "export const q = (d) => d.quit();\n"}
        self.assertFalse(residue_check(selenium).passed)

    def test_close_is_not_a_selenium_member(self):
        """Browser, BrowserContext and Page all have one; `quit` is the tell."""
        self.assertTrue(residue_check({"a.ts": "await driver.close();\n"}).passed)
        self.assertFalse(residue_check({"a.ts": "await driver.quit();\n"}).passed)

    def test_a_playwright_page_object_keeping_the_old_field_name_passes(self):
        page_object = {"pages/LoginPage.ts": (
            "import { Page } from '@playwright/test';\n"
            "export class LoginPage {\n"
            "  constructor(private driver: Page) {}\n"
            "  async open() { await this.driver.goto('/login'); }\n"
            "  async user() { return this.driver.locator('#user'); }\n"
            "  async title() { return this.driver.title(); }\n"
            "}\n")}
        self.assertTrue(residue_check(page_object).passed, residue_check(page_object).render())


class CommentsAreNotResidueTests(unittest.TestCase):
    """A TODO explaining what the Selenium did is the opposite of leaving it in."""

    def test_a_comment_mentioning_the_old_api_is_allowed(self):
        noted = {"a.ts": (
            "// TODO(review): the source called driver.manage().window().maximize();\n"
            "// this approximates it with viewport: null.\n"
            "export const viewport = null;\n")}
        self.assertTrue(residue_check(noted).passed, residue_check(noted).render())

    def test_a_trailing_comment_does_not_hide_real_residue(self):
        self.assertFalse(residue_check(
            {"a.ts": "await driver.quit(); // tidy up\n"}).passed)


if __name__ == "__main__":
    unittest.main()
