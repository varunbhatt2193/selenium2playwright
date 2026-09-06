import { Builder, WebDriver } from "selenium-webdriver";
import { Options } from "selenium-webdriver/chrome";
import { expect } from "chai";
import { WindowsPage } from "../pages/WindowsPage";

describe("Windows", function () {
  this.timeout(30000);
  let driver: WebDriver;
  let windowsPage: WindowsPage;

  before(async () => {
    const options = new Options();
    options.addArguments("--headless=new");
    driver = await new Builder().forBrowser("chrome").setChromeOptions(options).build();
    windowsPage = new WindowsPage(driver);
  });

  after(async () => {
    if (driver) await driver.quit();
  });

  it("checks the new window then returns to the original", async () => {
    await windowsPage.open();
    const original = await driver.getWindowHandle();
    await windowsPage.openNewWindow();
    try {
      expect(await windowsPage.getHeadingText()).to.equal("New Window");
    } finally {
      // Close only the child, then restore the original before using its POM again.
      await driver.close();
      await driver.switchTo().window(original);
    }
    expect(await windowsPage.getHeadingText()).to.equal("Opening a new window");
  });
});
