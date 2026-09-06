import { mkdtemp, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Builder, WebDriver } from "selenium-webdriver";
import { Options } from "selenium-webdriver/chrome";
import { expect } from "chai";
import { UploadPage } from "../pages/UploadPage";

describe("Upload", function () {
  this.timeout(30000);
  let driver: WebDriver | undefined;
  let uploadPage: UploadPage;

  before(async () => {
    const options = new Options();
    options.addArguments("--headless=new");
    driver = await new Builder().forBrowser("chrome").setChromeOptions(options).build();
    uploadPage = new UploadPage(driver);
  });

  after(async () => {
    if (driver) await driver.quit();
  });

  it("uploads a created file and verifies its name", async () => {
    // Each run owns a unique directory; the example needs no separate fixture file.
    const directory = await mkdtemp(join(tmpdir(), "s2p-upload-"));
    const filename = "conversion-eval.txt";
    try {
      const filePath = join(directory, filename);
      await writeFile(filePath, "Selenium to Playwright evaluation fixture.\n", "utf8");
      await uploadPage.open();
      await uploadPage.upload(filePath);
      expect(await uploadPage.getHeadingText()).to.equal("File Uploaded!");
      expect(await uploadPage.getUploadedFilename()).to.equal(filename);
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });
});
