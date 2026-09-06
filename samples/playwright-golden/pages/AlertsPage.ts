import { type Locator, type Page } from "@playwright/test";

// Independent reference fixture, authored without running the conversion graph.
export class AlertsPage {
  private readonly alertButton: Locator;
  private readonly confirmButton: Locator;
  // Tests can use await expect(resultMessage).toHaveText(...) to retry the assertion.
  readonly resultMessage: Locator;

  constructor(private readonly page: Page) {
    // Accessible names were verified against the demo page's actual button labels.
    this.alertButton = page.getByRole("button", { name: "Click for JS Alert", exact: true });
    this.confirmButton = page.getByRole("button", { name: "Click for JS Confirm", exact: true });
    this.resultMessage = page.locator("#result");
  }

  async open(): Promise<void> {
    // The shared Playwright config supplies the host through baseURL.
    await this.page.goto("/javascript_alerts");
  }

  async acceptAlert(): Promise<void> {
    // Register first: waiting until after an awaited click can stall on an open dialog.
    // The .then callback accepts it immediately; Promise.all awaits both operations.
    await Promise.all([
      this.page.waitForEvent("dialog").then((dialog) => dialog.accept()),
      this.alertButton.click(),
    ]);
  }

  async dismissConfirm(): Promise<void> {
    // Handle this dialog explicitly, preserving Cancel even though Playwright's
    // default with no listener is auto-dismiss. Both promises propagate failures.
    await Promise.all([
      this.page.waitForEvent("dialog").then((dialog) => dialog.dismiss()),
      this.confirmButton.click(),
    ]);
  }
}
