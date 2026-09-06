import { type Locator, type Page } from "@playwright/test";

export class UploadPage {
  readonly heading: Locator;
  readonly uploadedFilename: Locator;

  constructor(private readonly page: Page) {
    this.heading = page.getByRole("heading", { level: 3 });
    this.uploadedFilename = page.locator("#uploaded-files");
  }

  async open(): Promise<void> {
    await this.page.goto("/upload");
  }

  async upload(filePath: string): Promise<void> {
    // setInputFiles selects bytes for the form; the separate click submits them.
    await this.page.locator("#file-upload").setInputFiles(filePath);
    await this.page.getByRole("button", { name: "Upload", exact: true }).click();
  }
}
