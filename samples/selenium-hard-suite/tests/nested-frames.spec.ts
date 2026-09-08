import { Builder, WebDriver } from "selenium-webdriver";
import { Options } from "selenium-webdriver/chrome";
import { expect } from "chai";
import { NestedFramesPage } from "../pages/NestedFramesPage";

describe("Nested frames", function () {
  this.timeout(60000);
  let driver: WebDriver;
  let frames: NestedFramesPage;

  before(async () => {
    const options = new Options();
    options.addArguments("--headless=new");
    driver = await new Builder().forBrowser("chrome").setChromeOptions(options).build();
    frames = new NestedFramesPage(driver);
  });

  after(async () => {
    if (driver) await driver.quit();
  });

  it("reads the middle frame and then the bottom frame", async () => {
    await frames.open();
    // The order is the test: reading the bottom frame first would leave the
    // driver in the wrong document for the nested lookup that follows.
    await frames.enterTopFrame();
    expect(await frames.readMiddleFrame()).to.equal("MIDDLE");
    await frames.returnToTop();
    expect(await frames.readBottomFrame()).to.equal("BOTTOM");
  });
});
