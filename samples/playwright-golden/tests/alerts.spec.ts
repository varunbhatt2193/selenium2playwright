import { test, expect } from "@playwright/test";
import { AlertsPage } from "../pages/AlertsPage";

// Keep source test identities so parity can match each test and its assertion.
test.describe("JavaScript alerts", () => {
  let alertsPage: AlertsPage;

  test.beforeEach(async ({ page }) => {
    // Playwright supplies and cleans up an isolated page/context for each test.
    alertsPage = new AlertsPage(page);
    await alertsPage.open();
  });

  test("accepts a JavaScript alert", async () => {
    await alertsPage.acceptAlert();
    // Assert against a locator so Playwright can retry while the page updates.
    await expect(alertsPage.resultMessage).toHaveText("You successfully clicked an alert");
  });

  test("dismisses a JavaScript confirmation", async () => {
    await alertsPage.dismissConfirm();
    // This checks Cancel specifically; any nonempty result would be too permissive.
    await expect(alertsPage.resultMessage).toHaveText("You clicked: Cancel");
  });
});
