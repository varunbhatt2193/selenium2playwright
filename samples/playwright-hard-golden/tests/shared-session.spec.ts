import { test, expect, type Page } from "@playwright/test";
import { SecureAreaPage } from "../pages/SecureAreaPage";

// Playwright gives every test its own context, so a login in a beforeAll hook
// reaches nobody by default. Serial mode plus one explicitly shared page keeps
// the source's behaviour — including its order dependence — rather than hiding
// it behind a per-test login that would change what these tests prove.
// TODO(review): the durable fix is storageState from a global setup project;
// that is a suite-level change this single-file conversion cannot make.
test.describe.configure({ mode: "serial" });

test.describe("Secure area", () => {
  let page: Page;
  let securePage: SecureAreaPage;

  test.beforeAll(async ({ browser }) => {
    page = await browser.newPage();
    securePage = new SecureAreaPage(page);
    await securePage.login("tomsmith", "SuperSecretPassword!");
  });

  test.afterAll(async () => {
    await page.close();
  });

  test("lands on the secure area after the shared login", async () => {
    await expect(securePage.flash).toContainText("You logged into a secure area!");
  });

  test("still shows the secure area after a reload", async () => {
    await securePage.reload();
    await expect(securePage.heading).toHaveText("Secure Area");
  });
});
