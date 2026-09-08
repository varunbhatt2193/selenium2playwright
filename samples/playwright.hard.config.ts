import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./playwright-hard-golden/tests",
  use: {
    baseURL: "https://the-internet.herokuapp.com",
  },
});
