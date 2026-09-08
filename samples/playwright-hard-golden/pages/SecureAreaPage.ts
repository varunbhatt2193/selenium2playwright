import { type Locator, type Page } from "@playwright/test";
import { BasePage } from "./BasePage";

export class SecureAreaPage extends BasePage {
  readonly flash: Locator;
  readonly heading: Locator;

  constructor(page: Page) {
    super(page);
    this.flash = page.locator("#flash");
    this.heading = page.getByRole("heading", { level: 2 });
  }

  async login(username: string, password: string): Promise<void> {
    await this.visit("/login");
    await this.page.getByLabel("Username").fill(username);
    await this.page.getByLabel("Password").fill(password);
    await this.page.getByRole("button", { name: "Login" }).click();
  }

  async reload(): Promise<void> {
    await this.page.reload();
  }
}
