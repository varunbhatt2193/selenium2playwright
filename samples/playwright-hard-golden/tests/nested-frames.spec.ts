import { test, expect } from "@playwright/test";
import { NestedFramesPage } from "../pages/NestedFramesPage";

test.describe("Nested frames", () => {
  test("reads the middle frame and then the bottom frame", async ({ page }) => {
    const frames = new NestedFramesPage(page);
    await frames.open();
    // Order is no longer load-bearing; the reads are kept in the source's order
    // so the two assertions still describe the same behaviour.
    await expect(frames.middleContent).toHaveText("MIDDLE");
    await expect(frames.bottomBody).toHaveText("BOTTOM");
  });
});
