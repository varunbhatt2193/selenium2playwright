import { Builder, WebDriver } from "selenium-webdriver";
import { Options } from "selenium-webdriver/chrome";
import { expect } from "chai";
import { IframePage } from "../pages/IframePage";

describe("Iframe", function () {
  this.timeout(30000);
  let driver: WebDriver | undefined;
  let iframePage: IframePage;

  before(async () => {
    const options = new Options();
    options.addArguments("--headless=new");
    driver = await new Builder().forBrowser("chrome").setChromeOptions(options).build();
    iframePage = new IframePage(driver);
  });

  after(async () => {
    if (driver) await driver.quit();
  });

  it("reads the editor and returns to the parent page", async () => {
    await iframePage.open();
    expect(await iframePage.getEditorText()).to.equal("Your content goes here.");
    // This fails if the POM leaves Selenium inside the editor frame.
    expect(await iframePage.getHeadingText()).to.equal("An iFrame containing the TinyMCE WYSIWYG Editor");
  });
});
