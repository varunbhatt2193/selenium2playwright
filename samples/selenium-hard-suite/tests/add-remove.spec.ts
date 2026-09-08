import { Builder, WebDriver } from "selenium-webdriver";
import { Options } from "selenium-webdriver/chrome";
import { expect } from "chai";
import { AddRemovePage } from "../pages/AddRemovePage";

describe("Add and remove elements", function () {
  this.timeout(60000);
  let driver: WebDriver;
  let addRemove: AddRemovePage;

  before(async () => {
    const options = new Options();
    options.addArguments("--headless=new");
    driver = await new Builder().forBrowser("chrome").setChromeOptions(options).build();
    addRemove = new AddRemovePage(driver);
  });

  after(async () => {
    if (driver) await driver.quit();
  });

  it("adds five rows and then deletes every one of them", async () => {
    await addRemove.open();
    await addRemove.addElements(5);
    expect(await addRemove.countDeleteButtons()).to.equal(5);
    await addRemove.deleteAll();
    expect(await addRemove.countDeleteButtons()).to.equal(0);
  });
});
