import { mkdtemp, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test, expect } from "@playwright/test";
import { UploadPage } from "../pages/UploadPage";

test.describe("Upload", () => {
  test("uploads a created file and verifies its name", async ({ page }) => {
    const directory = await mkdtemp(join(tmpdir(), "s2p-upload-"));
    const filename = "conversion-eval.txt";
    try {
      const filePath = join(directory, filename);
      await writeFile(filePath, "Selenium to Playwright evaluation fixture.\n", "utf8");
      const uploadPage = new UploadPage(page);
      await uploadPage.open();
      await uploadPage.upload(filePath);
      // Exact filename catches a different file being submitted successfully.
      await expect(uploadPage.heading).toHaveText("File Uploaded!");
      await expect(uploadPage.uploadedFilename).toHaveText(filename);
    } finally {
      // Runner fixtures clean up the browser; filesystem resources still belong to us.
      await rm(directory, { recursive: true, force: true });
    }
  });
});
