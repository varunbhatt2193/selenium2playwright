import { type Locator, type Page } from "@playwright/test";
import { BasePage } from "./BasePage";

export class DynamicControlsPage extends BasePage {
  readonly checkbox: Locator;
  readonly checkboxToggle: Locator;
  readonly message: Locator;
  readonly textField: Locator;
  readonly enableToggle: Locator;

  constructor(page: Page) {
    super(page);
    this.checkbox = page.locator("#checkbox-example input[type='checkbox']");
    this.checkboxToggle = page.locator("#checkbox-example button");
    this.message = page.locator("#message");
    this.textField = page.locator("#input-example input");
    this.enableToggle = page.locator("#input-example button");
  }

  async open(): Promise<void> {
    // No implicit-wait configuration: each assertion carries its own timeout,
    // and an absence check no longer pays for one set globally in open().
    await this.visit("/dynamic_controls");
  }

  async removeCheckbox(): Promise<void> {
    await this.checkboxToggle.click();
  }

  async addCheckbox(): Promise<void> {
    await this.checkboxToggle.click();
  }

  async enableTextField(): Promise<void> {
    await this.enableToggle.click();
  }

  async typeIntoTextField(text: string): Promise<void> {
    // fill() waits for the field to be editable, so the poll for "enabled yet?"
    // and the paired clear() both disappear here.
    await this.textField.fill(text);
  }
}
