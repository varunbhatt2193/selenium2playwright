import { By, WebDriver, until } from "selenium-webdriver";

export class IframePage {
  constructor(private readonly driver: WebDriver) {}

  async open(): Promise<void> {
    await this.driver.get("https://the-internet.herokuapp.com/iframe");
  }

  async getEditorText(): Promise<string> {
    // TinyMCE creates this iframe asynchronously; its generated ID is not stable.
    await this.driver.wait(until.ableToSwitchToFrame(By.css(".tox-edit-area iframe")), 15000);
    try {
      const body = await this.driver.wait(until.elementLocated(By.id("tinymce")), 5000);
      await this.driver.wait(until.elementTextMatches(body, /\S/), 5000);
      return await body.getText();
    } finally {
      // Restore driver state even if reading fails, so later parent lookups are valid.
      await this.driver.switchTo().defaultContent();
    }
  }

  async getHeadingText(): Promise<string> {
    const heading = await this.driver.findElement(By.css("h3"));
    return heading.getText();
  }
}
