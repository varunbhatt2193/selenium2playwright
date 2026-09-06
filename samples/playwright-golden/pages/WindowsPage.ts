import { type Locator, type Page } from "@playwright/test";

export class WindowsPage {
  readonly heading: Locator;

  constructor(private readonly page: Page) {
    this.heading = page.getByRole("heading", { level: 3 });
  }

  async open(): Promise<void> {
    await this.page.goto("/windows");
  }

  async openNewWindow(): Promise<Page> {
    // Subscribe before clicking; waiting afterwards could miss the popup event.
    const [popup] = await Promise.all([
      this.page.waitForEvent("popup"),
      this.page.getByRole("link", { name: "Click Here", exact: true }).click(),
    ]);
    return popup; // The original Page stays available without switching handles.
  }
}
