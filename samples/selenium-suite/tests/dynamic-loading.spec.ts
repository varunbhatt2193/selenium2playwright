import { Builder, WebDriver } from "selenium-webdriver";
import { Options } from "selenium-webdriver/chrome";
import { expect } from "chai";
import { DynamicLoadingPage } from "../pages/DynamicLoadingPage";

describe("Dynamic loading", function () {
  this.timeout(30000);
  let driver: WebDriver | undefined;
  let loadingPage: DynamicLoadingPage;

  before(async () => {
    const options = new Options();
    options.addArguments("--headless=new");
    driver = await new Builder().forBrowser("chrome").setChromeOptions(options).build();
    loadingPage = new DynamicLoadingPage(driver);
  });

  after(async () => {
    if (driver) await driver.quit();
  });

  it("waits for the dynamically inserted result", async () => {
    await loadingPage.open();
    await loadingPage.start();
    // The POM waits for both existence and visibility before returning a string.
    expect(await loadingPage.getFinishedText()).to.equal("Hello World!");
  });
});
