import { test, expect } from "@playwright/test";
import { HoversPage } from "../pages/HoversPage";

test.describe("Hovers", () => {
  test("reveals only the hovered avatar's caption", async ({ page }) => {
    const hovers = new HoversPage(page);
    await hovers.open();
    await hovers.hoverOverAvatar(1);
    await expect(hovers.captionNames.nth(1)).toHaveText("name: user2");
    await expect(hovers.profileLinks.nth(1)).toBeVisible();
    await expect(hovers.profileLinks.nth(2)).toBeHidden();
  });
});
