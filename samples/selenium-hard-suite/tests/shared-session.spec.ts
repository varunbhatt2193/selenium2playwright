import { Builder, WebDriver } from "selenium-webdriver";
import { Options } from "selenium-webdriver/chrome";
import { expect } from "chai";
import { SecureAreaPage } from "../pages/SecureAreaPage";

/**
 * Legacy style, deliberately: not one `await` in the file. Hooks and tests
 * return promises and chain with .then(), the way suites written against the
 * old promise manager read. The browser and the login both happen once, in
 * `before`, and every test below inherits whatever session that hook left.
 */
describe("Secure area", function () {
  this.timeout(60000);
  let driver: WebDriver;
  let securePage: SecureAreaPage;

  before(function () {
    const options = new Options();
    options.addArguments("--headless=new");
    return new Builder()
      .forBrowser("chrome")
      .setChromeOptions(options)
      .build()
      .then((built) => {
        driver = built;
        securePage = new SecureAreaPage(driver);
        return securePage.login("tomsmith", "SuperSecretPassword!");
      });
  });

  after(function () {
    return driver ? driver.quit() : Promise.resolve();
  });

  it("lands on the secure area after the shared login", function () {
    return securePage.getFlashText().then((flash) => {
      expect(flash).to.contain("You logged into a secure area!");
    });
  });

  // No login of its own: this test only passes because the one above ran first
  // in the same browser. Nothing in the file states that dependency.
  it("still shows the secure area after a reload", function () {
    return securePage
      .reload()
      .then(() => securePage.getHeadingText())
      .then((heading) => {
        expect(heading).to.equal("Secure Area");
      });
  });
});
