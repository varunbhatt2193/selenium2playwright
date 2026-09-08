import { By, WebDriver, WebElement, until } from "selenium-webdriver";

/**
 * The wait toolbox every page object in this suite inherits.
 *
 * Suites like this one exist because Selenium does not wait for you: each
 * helper below is a hand-rolled version of a guarantee Playwright gives for
 * free. Converting them mechanically would carry all of that machinery into a
 * framework that no longer needs it.
 */
export abstract class BasePage {
  protected static readonly TIMEOUT = 10000;

  constructor(protected readonly driver: WebDriver) {}

  protected async visit(path: string): Promise<void> {
    await this.driver.get(`https://the-internet.herokuapp.com${path}`);
    await this.waitForPageLoad();
  }

  /** Poll document.readyState — this suite's stand-in for "the page settled". */
  protected async waitForPageLoad(): Promise<void> {
    await this.driver.wait(async () => {
      const state = await this.driver.executeScript<string>("return document.readyState");
      return state === "complete";
    }, BasePage.TIMEOUT, "the document never reached readyState complete");
  }

  /** Located, visible, enabled, then clicked — the three guards, every time. */
  protected async waitAndClick(locator: By): Promise<void> {
    const element = await this.driver.wait(until.elementLocated(locator), BasePage.TIMEOUT);
    await this.driver.wait(until.elementIsVisible(element), BasePage.TIMEOUT);
    await this.driver.wait(until.elementIsEnabled(element), BasePage.TIMEOUT);
    await element.click();
  }

  protected async waitForText(locator: By): Promise<string> {
    const element = await this.driver.wait(until.elementLocated(locator), BasePage.TIMEOUT);
    await this.driver.wait(until.elementIsVisible(element), BasePage.TIMEOUT);
    return element.getText();
  }

  /**
   * The generic escape hatch: poll a caller-supplied predicate until it holds.
   * Used wherever `until.*` has no condition for what the test actually means.
   */
  protected async waitUntil(condition: () => Promise<boolean>, message: string): Promise<void> {
    await this.driver.wait(async () => condition(), BasePage.TIMEOUT, message);
  }

  /** findElements never throws on absence; callers use its length as a presence check. */
  protected async findAll(locator: By): Promise<WebElement[]> {
    return this.driver.findElements(locator);
  }
}
