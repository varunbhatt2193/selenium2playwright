import { test, expect } from "@playwright/test";
import { IframePage } from "../pages/IframePage";

test.describe("Iframe", () => {
  test("reads the editor and returns to the parent page", async ({ page }) => {
    const iframePage = new IframePage(page);
    await iframePage.open();
    // CDN initialization can take longer than the default assertion timeout.
    await expect(iframePage.editorBody).toHaveText("Your content goes here.", { timeout: 15000 });
    await expect(iframePage.heading).toHaveText("An iFrame containing the TinyMCE WYSIWYG Editor");
  });
});
