import { By, WebDriver, until } from "selenium-webdriver";

export class WindowsPage {
  constructor(private readonly driver: WebDriver) {}

  async open(): Promise<void> {
    await this.driver.get("https://the-internet.herokuapp.com/windows");
  }

  async openNewWindow(): Promise<void> {
    const before = await this.driver.getAllWindowHandles();
    await this.driver.findElement(By.linkText("Click Here")).click();
    // Identify the added handle by set difference; handle ordering is not a contract.
    const added = await this.driver.wait(async () => {
      const handles = await this.driver.getAllWindowHandles();
      return handles.find((handle) => !before.includes(handle)) || false;
    }, 10000);
    // wait resolves on a truthy value, but its TypeScript return type still includes false.
    if (!added) throw new Error("No new window handle was found");
    await this.driver.switchTo().window(added);
  }

  async getHeadingText(): Promise<string> {
    // Uses the currently selected window, which the test deliberately changes.
    const heading = await this.driver.wait(until.elementLocated(By.css("h3")), 10000);
    await this.driver.wait(until.elementIsVisible(heading), 10000);
    return heading.getText();
  }
}
