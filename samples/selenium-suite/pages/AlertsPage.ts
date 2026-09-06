import { By, WebDriver, until } from "selenium-webdriver";

// Source fixture: the page object performs actions; tests check their outcomes.
export class AlertsPage {
  private readonly url = "https://the-internet.herokuapp.com/javascript_alerts";
  // These handlers identify the two buttons in the demo page's inspected HTML.
  private readonly alertButton = By.css("button[onclick='jsAlert()']");
  private readonly confirmButton = By.css("button[onclick='jsConfirm()']");
  private readonly resultMessage = By.id("result");

  constructor(private readonly driver: WebDriver) {}

  async open(): Promise<void> {
    await this.driver.get(this.url);
    await this.driver.wait(until.elementLocated(this.alertButton), 5000);
  }

  async acceptAlert(): Promise<void> {
    await this.driver.findElement(this.alertButton).click();
    // Browser dialogs are outside the DOM; wait for WebDriver's Alert object.
    const dialog = await this.driver.wait(until.alertIsPresent(), 5000);
    await dialog.accept();
  }

  async dismissConfirm(): Promise<void> {
    await this.driver.findElement(this.confirmButton).click();
    const dialog = await this.driver.wait(until.alertIsPresent(), 5000);
    // Cancel is the behavior under test; accepting would choose the other branch.
    await dialog.dismiss();
  }

  async getResultText(): Promise<string> {
    const result = await this.driver.findElement(this.resultMessage);
    // The result element exists before any action, but initially contains no text.
    // Each test opens a fresh page before its action, so old text cannot satisfy this wait.
    await this.driver.wait(until.elementTextMatches(result, /\S/), 5000);
    return result.getText();
  }
}
