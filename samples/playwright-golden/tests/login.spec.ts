import { test, expect } from "@playwright/test";
import { LoginPage } from "../pages/LoginPage";

test.describe("Login", () => {
  let loginPage: LoginPage;

  test.beforeEach(async ({ page }) => {
    loginPage = new LoginPage(page);
    await loginPage.open();
  });

  test("logs in with valid credentials", async () => {
    await loginPage.login("tomsmith", "SuperSecretPassword!");
    await expect(loginPage.flashMessage).toContainText(
      "You logged into a secure area!"
    );
  });

  test("rejects invalid credentials", async () => {
    await loginPage.login("tomsmith", "wrong-password");
    await expect(loginPage.flashMessage).toContainText(
      "Your password is invalid!"
    );
  });
});
