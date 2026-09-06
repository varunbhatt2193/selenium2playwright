import { test, expect } from "@playwright/test";
import { DynamicLoadingPage } from "../pages/DynamicLoadingPage";

test.describe("Dynamic loading", () => {
  test("waits for the dynamically inserted result", async ({ page }) => {
    const loadingPage = new DynamicLoadingPage(page);
    await loadingPage.open();
    await loadingPage.start();
    // The demo waits ~5 seconds. A condition-based 10s budget avoids a fixed sleep.
    await expect(loadingPage.finishedText).toBeVisible({ timeout: 10000 });
    await expect(loadingPage.finishedText).toHaveText("Hello World!");
  });
});
