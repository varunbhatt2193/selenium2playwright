import { test, expect } from "@playwright/test";
import { AddRemovePage } from "../pages/AddRemovePage";

test.describe("Add and remove elements", () => {
  test("adds five rows and then deletes every one of them", async ({ page }) => {
    const addRemove = new AddRemovePage(page);
    await addRemove.open();
    await addRemove.addElements(5);
    await expect(addRemove.deleteButtons).toHaveCount(5);
    await addRemove.deleteAll();
    await expect(addRemove.deleteButtons).toHaveCount(0);
  });
});
