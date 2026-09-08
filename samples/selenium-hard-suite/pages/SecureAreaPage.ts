import { By } from "selenium-webdriver";
import { BasePage } from "./BasePage";

const USERNAME = By.id("username");
const PASSWORD = By.id("password");
const SUBMIT = By.css("button[type='submit']");
const FLASH = By.id("flash");
const HEADING = By.css("#content h2");

export class SecureAreaPage extends BasePage {
  /** One login for the whole file: the suite reuses the session it leaves behind. */
  async login(username: string, password: string): Promise<void> {
    await this.visit("/login");
    await this.driver.findElement(USERNAME).sendKeys(username);
    await this.driver.findElement(PASSWORD).sendKeys(password);
    await this.waitAndClick(SUBMIT);
  }

  async reload(): Promise<void> {
    await this.driver.navigate().refresh();
    await this.waitForPageLoad();
  }

  async getFlashText(): Promise<string> {
    return this.waitForText(FLASH);
  }

  async getHeadingText(): Promise<string> {
    return this.waitForText(HEADING);
  }
}
