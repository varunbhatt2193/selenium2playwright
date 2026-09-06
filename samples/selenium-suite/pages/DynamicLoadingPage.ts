import { By, WebDriver, until } from "selenium-webdriver";

export class DynamicLoadingPage {
  constructor(private readonly driver: WebDriver) {}

  async open(): Promise<void> {
    await this.driver.get("https://the-internet.herokuapp.com/dynamic_loading/2");
  }

  async start(): Promise<void> {
    await this.driver.findElement(By.css("#start button")).click();
  }

  async getFinishedText(): Promise<string> {
    // Example 2 inserts the node later. Locating it immediately can throw.
    const finished = await this.driver.wait(until.elementLocated(By.css("#finish h4")), 10000);
    await this.driver.wait(until.elementIsVisible(finished), 10000);
    return finished.getText();
  }
}
