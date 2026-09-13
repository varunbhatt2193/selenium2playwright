"""Gate 3's own rule: a browser dialog has to be handled the moment it opens.

On 2026-09-13 a live conversion of AlertsPage.ts scored 4/4 gates, a critic
pass and no TODOs, and deadlocked in a real browser six runs out of six: it
waited for the dialog, awaited the click that opened it, then accepted it. The
open dialog blocks the click, so the accept is never reached. No published
ESLint rule knows this, so `sandbox/eslint.config.mjs` carries one.

These run the real ESLint over the sandbox config, like the compile gate tests
run the real tsc.
"""

import unittest

from selenium2playwright.validators.lint import ESLINT, lint_check

RULE = "error/s2p/dialog-handled-on-arrival"

PAGE = """import {{ type Locator, type Page }} from "@playwright/test";

export class AlertsPage {{
  private readonly alertButton: Locator;

  constructor(private readonly page: Page) {{
    this.alertButton = page.getByRole("button", {{ name: "Click for JS Alert" }});
  }}

  async acceptAlert(): Promise<void> {{
{body}
  }}
}}
"""

STALLS = {
    "the live conversion: wait, await the click, then accept": """\
    const dialogPromise = this.page.waitForEvent("dialog");
    await this.alertButton.click();
    const dialog = await dialogPromise;
    await dialog.accept();""",
    "Promise.all, then accept in a later statement": """\
    const [dialog] = await Promise.all([this.page.waitForEvent("dialog"), this.alertButton.click()]);
    await dialog.accept();""",
    "awaiting the dialog before the action": """\
    const dialog = await this.page.waitForEvent("dialog");
    await dialog.accept();
    await this.alertButton.click();""",
}

HANDLED = {
    "the golden: .then inside Promise.all": """\
    await Promise.all([
      this.page.waitForEvent("dialog").then((dialog) => dialog.accept()),
      this.alertButton.click(),
    ]);""",
    "a handler registered before the action": """\
    this.page.once("dialog", (dialog) => void dialog.accept());
    await this.alertButton.click();""",
    "a popup, which is not a dialog": """\
    await Promise.all([this.page.waitForEvent("popup"), this.alertButton.click()]);""",
}


@unittest.skipUnless(ESLINT.exists(), "the sandbox toolchain is not installed (npm ci in sandbox/)")
class DialogOrderingTests(unittest.TestCase):
    def rule_hits(self, body: str) -> list:
        report = lint_check({"pages/AlertsPage.ts": PAGE.format(body=body)})
        return [f for f in report.findings if f.code == RULE]

    def test_every_stalling_shape_blocks_the_gate(self):
        for what, body in STALLS.items():
            with self.subTest(what=what):
                hits = self.rule_hits(body)
                self.assertTrue(hits, f"{what} passed the lint gate")
                self.assertIn("stalls", hits[0].message)

    def test_a_dialog_handled_on_arrival_passes(self):
        for what, body in HANDLED.items():
            with self.subTest(what=what):
                self.assertEqual(self.rule_hits(body), [])

    def test_a_blocking_finding_fails_the_gate(self):
        report = lint_check({"pages/AlertsPage.ts": PAGE.format(body=STALLS[next(iter(STALLS))])})
        self.assertFalse(report.passed)


if __name__ == "__main__":
    unittest.main()
