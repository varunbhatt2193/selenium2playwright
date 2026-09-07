// Fixture for step 7.2 (human-in-the-loop), NOT part of the pinned eval suite in
// selenium-suite/. It is deliberately written the way older Selenium suites are:
// one login shared by every test, and injected JavaScript standing in for things
// WebDriver could not do directly. Both have more than one correct Playwright
// translation, which is exactly what the agent must stop and ask about.
//
// `npm test` only runs selenium-suite/tests, so this file is type-checked but
// never executed against the live site.
import { Builder, By, WebDriver, until } from "selenium-webdriver";
import { Options } from "selenium-webdriver/chrome";
import { expect } from "chai";

describe("Secure area", function () {
  this.timeout(30000);

  let driver: WebDriver;

  before(async () => {
    const options = new Options();
    options.addArguments("--headless=new");
    driver = await new Builder().forBrowser("chrome").setChromeOptions(options).build();
    // Logging in ONCE: every test below inherits this session from the shared driver.
    await driver.get("https://the-internet.herokuapp.com/login");
    await driver.findElement(By.id("username")).sendKeys("tomsmith");
    await driver.findElement(By.id("password")).sendKeys("SuperSecretPassword!");
    await driver.findElement(By.css("button[type='submit']")).click();
    await driver.wait(until.elementLocated(By.id("flash")), 5000);
  });

  after(async () => {
    if (driver) await driver.quit();
  });

  it("stays on the secure page", async () => {
    // No login here: it relies on the session created in `before`.
    await driver.get("https://the-internet.herokuapp.com/secure");
    const heading = await driver.findElement(By.css("h2")).getText();
    expect(heading).to.equal("Secure Area");
  });

  it("logs out from the footer link", async () => {
    await driver.get("https://the-internet.herokuapp.com/secure");
    const logout = await driver.findElement(By.css("a.button.secondary"));
    // Scrolling by injected script because the footer link sits below the fold.
    await driver.executeScript("arguments[0].scrollIntoView(true);", logout);
    await logout.click();
    await driver.wait(until.elementLocated(By.id("flash")), 5000);
    const flash = await driver.findElement(By.id("flash")).getText();
    expect(flash).to.contain("You logged out of the secure area!");
  });
});
