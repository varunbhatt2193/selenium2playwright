import { Builder, WebDriver } from "selenium-webdriver";
import { Options } from "selenium-webdriver/chrome";
import { expect } from "chai";
import { DynamicControlsPage } from "../pages/DynamicControlsPage";

describe("Dynamic controls", function () {
  this.timeout(60000);
  let driver: WebDriver;
  let controls: DynamicControlsPage;

  beforeEach(async () => {
    const options = new Options();
    options.addArguments("--headless=new");
    driver = await new Builder().forBrowser("chrome").setChromeOptions(options).build();
    controls = new DynamicControlsPage(driver);
    await controls.open();
  });

  afterEach(async () => {
    if (driver) await driver.quit();
  });

  it("removes the checkbox and reports it gone", async () => {
    await controls.removeCheckbox();
    expect(await controls.getMessage()).to.equal("It's gone!");
    // Absence, asserted through the implicit wait the page object configured.
    expect(await controls.isCheckboxPresent()).to.equal(false);
  });

  it("enables the text field and accepts input", async () => {
    await controls.enableTextField();
    expect(await controls.getMessage()).to.equal("It's enabled!");
    expect(await controls.typeIntoTextField("hello")).to.equal("hello");
  });
});
