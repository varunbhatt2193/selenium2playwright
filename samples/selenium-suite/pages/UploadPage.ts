import { By, WebDriver, until } from "selenium-webdriver";

export class UploadPage {
  constructor(private readonly driver: WebDriver) {}

  async open(): Promise<void> {
    await this.driver.get("https://the-internet.herokuapp.com/upload");
  }

  async upload(filePath: string): Promise<void> {
    // WebDriver sends an absolute path directly to the file input; no OS dialog is needed.
    await this.driver.findElement(By.id("file-upload")).sendKeys(filePath);
    await this.driver.findElement(By.id("file-submit")).click();
    await this.driver.wait(until.elementLocated(By.id("uploaded-files")), 10000);
  }

  async getHeadingText(): Promise<string> {
    return this.driver.findElement(By.css("h3")).getText();
  }

  async getUploadedFilename(): Promise<string> {
    return this.driver.findElement(By.id("uploaded-files")).getText();
  }
}
