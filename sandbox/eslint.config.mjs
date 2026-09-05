// Lint rules the agent's OUTPUT must satisfy (gate 3 of 4).
//
// "Flat config" = ESLint's modern format: this file exports a plain array of
// settings objects; later objects override earlier ones. No .eslintrc, no
// magic lookup — the Python wrapper points ESLint at this file explicitly.
//
// Why typed linting: `no-floating-promises` has to know that
// `page.getByLabel(...).fill(...)` returns a Promise before it can complain
// that nobody awaited it. Plain ESLint sees only syntax; typescript-eslint
// asks the TypeScript checker. `projectService` finds the tsconfig.json
// nearest to each linted file — the per-run work/<id>/tsconfig.json that
// compile.py already writes.

import { defineConfig } from "eslint/config";
import tseslint from "typescript-eslint";
import playwright from "eslint-plugin-playwright";

export default defineConfig(
  // 1. Teach ESLint to parse TypeScript with type information.
  {
    files: ["**/*.ts"],
    extends: [tseslint.configs.base],
    languageOptions: {
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    // Deliberately NOT the full "recommended" set: generated code would drown
    // the critic in style nits. Only rules that name a real conversion bug.
    rules: {
      // #1 bug class: a Promise created and dropped — the test races ahead.
      "@typescript-eslint/no-floating-promises": "error",
      // `if (locator.isVisible())` — a Promise used as a boolean is always truthy.
      "@typescript-eslint/no-misused-promises": ["error", { checksVoidReturn: false }],
      // `await page.locator(...)` — awaiting something that is not a Promise
      // is usually a Selenium habit (every WebDriver call was async).
      "@typescript-eslint/await-thenable": "error",
    },
  },

  // 2. Playwright's own rules. Its "recommended" preset is mostly warnings;
  //    the wrapper reports errors AND warnings, so all of these reach the critic.
  {
    files: ["**/*.ts"],
    extends: [playwright.configs["flat/recommended"]],
    rules: {
      // `expect(await el.textContent()).toBe(x)` → `await expect(el).toHaveText(x)`
      // Web-first assertions retry; the Selenium-style one-shot read does not.
      "playwright/prefer-web-first-assertions": "error",
      "playwright/missing-playwright-await": "error",
      // Page objects legitimately have no expect() calls; do not flag them.
      "playwright/expect-expect": "off",
    },
  },
);
