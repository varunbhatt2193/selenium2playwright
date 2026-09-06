import { test, expect } from "@playwright/test";
import { WindowsPage } from "../pages/WindowsPage";

test.describe("Windows", () => {
  test("checks the new window then returns to the original", async ({ page }) => {
    const windowsPage = new WindowsPage(page);
    await windowsPage.open();
    const popup = await windowsPage.openNewWindow();
    try {
      await expect(popup.getByRole("heading", { level: 3 })).toHaveText("New Window");
    } finally {
      await popup.close(); // The original Page and its locators remain valid.
    }
    await expect(windowsPage.heading).toHaveText("Opening a new window");
  });
});
