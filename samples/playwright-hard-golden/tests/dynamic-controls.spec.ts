import { test, expect } from "@playwright/test";
import { DynamicControlsPage } from "../pages/DynamicControlsPage";

test.describe("Dynamic controls", () => {
  test("removes the checkbox and reports it gone", async ({ page }) => {
    const controls = new DynamicControlsPage(page);
    await controls.open();
    await controls.removeCheckbox();
    await expect(controls.message).toHaveText("It's gone!");
    // Absence with a retrying timeout, which is what the implicit wait bought.
    await expect(controls.checkbox).toHaveCount(0);
  });

  test("enables the text field and accepts input", async ({ page }) => {
    const controls = new DynamicControlsPage(page);
    await controls.open();
    await controls.enableTextField();
    await expect(controls.message).toHaveText("It's enabled!");
    await controls.typeIntoTextField("hello");
    await expect(controls.textField).toHaveValue("hello");
  });
});
