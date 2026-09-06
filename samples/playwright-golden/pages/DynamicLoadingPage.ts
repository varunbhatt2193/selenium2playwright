import { type Locator, type Page } from "@playwright/test";

export class DynamicLoadingPage {
  readonly finishedText: Locator;

  constructor(private readonly page: Page) {
    // Creating a locator does not query the DOM; the node may appear after Start.
    this.finishedText = page.locator("#finish h4");
  }

  async open(): Promise<void> {
    await this.page.goto("/dynamic_loading/2");
  }

  async start(): Promise<void> {
    await this.page.getByRole("button", { name: "Start", exact: true }).click();
  }
}
