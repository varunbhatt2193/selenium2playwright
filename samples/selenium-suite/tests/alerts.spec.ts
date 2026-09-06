import { Builder, WebDriver } from "selenium-webdriver";
import { Options } from "selenium-webdriver/chrome";
import { expect } from "chai";
import { AlertsPage } from "../pages/AlertsPage";

describe("JavaScript alerts", function () {
  this.timeout(30000);

  let driver: WebDriver | undefined;
  let alertsPage: AlertsPage;

  before(async () => {
    // Browser ownership belongs to the test suite; headless keeps local/CI runs quiet.
    const options = new Options();
    // Retain the Chrome-specific type; addArguments() is typed as returning base options.
    options.addArguments("--headless=new");
    driver = await new Builder()
      .forBrowser("chrome")
      .setChromeOptions(options)
      .build();
    alertsPage = new AlertsPage(driver);
  });

  after(async () => {
    // If browser creation failed, preserve that error instead of failing cleanup too.
    if (driver) await driver.quit();
  });

  beforeEach(async () => {
    // Reset the result text before each action; the POM waits for nonempty text.
    await alertsPage.open();
  });

  it("accepts a JavaScript alert", async () => {
    await alertsPage.acceptAlert();
    const result = await alertsPage.getResultText();
    expect(result).to.equal("You successfully clicked an alert");
  });

  it("dismisses a JavaScript confirmation", async () => {
    await alertsPage.dismissConfirm();
    const result = await alertsPage.getResultText();
    // An accidental accept produces Ok, so exact equality catches the wrong branch.
    expect(result).to.equal("You clicked: Cancel");
  });
});
