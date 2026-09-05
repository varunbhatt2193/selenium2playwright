import { Builder, WebDriver } from "selenium-webdriver";
import { expect } from "chai";
import { LoginPage } from "../pages/LoginPage";

describe("Login", function () {
  this.timeout(30000);

  let driver: WebDriver;
  let loginPage: LoginPage;

  before(async () => {
    driver = await new Builder().forBrowser("chrome").build();
    loginPage = new LoginPage(driver);
  });

  after(async () => {
    await driver.quit();
  });

  beforeEach(async () => {
    await loginPage.open();
  });

  it("logs in with valid credentials", async () => {
    await loginPage.login("tomsmith", "SuperSecretPassword!");
    const flash = await loginPage.getFlashText();
    expect(flash).to.contain("You logged into a secure area!");
  });

  it("rejects invalid credentials", async () => {
    await loginPage.login("tomsmith", "wrong-password");
    const flash = await loginPage.getFlashText();
    expect(flash).to.contain("Your password is invalid!");
  });
});
