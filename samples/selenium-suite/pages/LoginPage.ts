import { By, WebDriver, until } from "selenium-webdriver";

export class LoginPage {
  private readonly url = "https://the-internet.herokuapp.com/login";
  private readonly usernameInput = By.id("username");
  private readonly passwordInput = By.id("password");
  private readonly loginButton = By.css("button[type='submit']");
  private readonly flashMessage = By.id("flash");

  constructor(private readonly driver: WebDriver) {}

  async open(): Promise<void> {
    await this.driver.get(this.url);
    await this.driver.wait(until.elementLocated(this.usernameInput), 5000);
  }

  async login(username: string, password: string): Promise<void> {
    await this.driver.findElement(this.usernameInput).sendKeys(username);
    await this.driver.findElement(this.passwordInput).sendKeys(password);
    await this.driver.findElement(this.loginButton).click();
  }

  async getFlashText(): Promise<string> {
    const flash = await this.driver.wait(
      until.elementLocated(this.flashMessage),
      5000
    );
    return flash.getText();
  }
}
