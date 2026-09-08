import { type Page } from "@playwright/test";

/**
 * What is left of the Selenium BasePage once Playwright's guarantees replace it.
 *
 * Ledger for the five inherited helpers, all removed on purpose:
 *   waitForPageLoad  removed — goto() already resolves on the load event.
 *   waitAndClick     removed — click() waits for attached, visible, stable,
 *                    enabled and hit-testable before it acts.
 *   waitForText      removed — page objects expose Locators and the tests use
 *                    web-first assertions, which retry against the live DOM.
 *   waitUntil        removed — expect.poll()/expect(...).toPass() cover the
 *                    remaining ad-hoc polling, in the test that needs it.
 *   findAll          removed — presence is an assertion (toHaveCount), not a
 *                    number the page object computes.
 */
export abstract class BasePage {
  constructor(protected readonly page: Page) {}

  protected async visit(path: string): Promise<void> {
    await this.page.goto(path);
  }
}
