import { Builder, WebDriver } from "selenium-webdriver";
import { Options } from "selenium-webdriver/chrome";
import { expect } from "chai";
import { HoversPage } from "../pages/HoversPage";

describe("Hovers", function () {
  this.timeout(60000);
  let driver: WebDriver;
  let hovers: HoversPage;

  before(async () => {
    const options = new Options();
    options.addArguments("--headless=new");
    driver = await new Builder().forBrowser("chrome").setChromeOptions(options).build();
    hovers = new HoversPage(driver);
  });

  after(async () => {
    if (driver) await driver.quit();
  });

  it("reveals only the hovered avatar's caption", async () => {
    await hovers.open();
    await hovers.hoverOverAvatar(1);
    expect(await hovers.getCaptionName(1)).to.equal("name: user2");
    expect(await hovers.isProfileLinkVisible(1)).to.equal(true);
    // The other captions stay hidden: the pointer is over exactly one figure.
    expect(await hovers.isProfileLinkVisible(2)).to.equal(false);
  });
});
