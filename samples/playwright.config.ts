import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./playwright-golden/tests",
  use: {
    baseURL: "https://the-internet.herokuapp.com",
  },
});
