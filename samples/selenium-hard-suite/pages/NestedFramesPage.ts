import { By, WebDriver, until } from "selenium-webdriver";

/**
 * /nested_frames is a frameset holding frame-top (itself a frameset of
 * left/middle/right) and frame-bottom. Every method below moves the driver's
 * ONE current frame, so the order in which the test calls them matters.
 */
export class NestedFramesPage {
  constructor(private readonly driver: WebDriver) {}

  async open(): Promise<void> {
    await this.driver.get("https://the-internet.herokuapp.com/nested_frames");
    await this.driver.switchTo().defaultContent();
  }

  /** Leaves the driver inside frame-top; the caller must call returnToTop(). */
  async enterTopFrame(): Promise<void> {
    const top = await this.driver.wait(until.elementLocated(By.name("frame-top")), 10000);
    await this.driver.switchTo().frame(top);
  }

  /** Only valid while the driver is already inside frame-top. */
  async readMiddleFrame(): Promise<string> {
    const middle = await this.driver.findElement(By.name("frame-middle"));
    await this.driver.switchTo().frame(middle);
    const content = await this.driver.wait(until.elementLocated(By.id("content")), 10000);
    return content.getText();
  }

  async returnToTop(): Promise<void> {
    await this.driver.switchTo().defaultContent();
  }

  /** Only valid from the top document; call returnToTop() first. */
  async readBottomFrame(): Promise<string> {
    const bottom = await this.driver.findElement(By.name("frame-bottom"));
    await this.driver.switchTo().frame(bottom);
    const body = await this.driver.findElement(By.css("body"));
    return body.getText();
  }
}
