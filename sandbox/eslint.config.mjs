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

// A rule no published plugin has, written for the one Playwright bug the gates
// passed on a live run (2026-09-13): AlertsPage.ts scored 4/4 and deadlocked
// in a browser six times out of six.
//
//   const dialogPromise = page.waitForEvent("dialog");
//   await button.click();              // never returns: the open dialog blocks it
//   (await dialogPromise).accept();    // never reached
//
// A dialog blocks the page until something handles it, and the action that
// opened it waits on the page. So the handler has to run the moment the event
// fires, not in a later statement: `.then(dialog => dialog.accept())` chained
// straight onto the wait (inside Promise.all with the action), or
// `page.once("dialog", ...)` registered before it. Awaiting the wait and
// handling the dialog afterwards stalls however it is arranged, Promise.all
// included, because the action inside it never resolves.
const dialogHandledOnArrival = {
  meta: {
    type: "problem",
    messages: {
      stall:
        'waitForEvent("dialog") without .then(dialog => ...) chained onto it stalls: the open dialog blocks the ' +
        "action that opened it, so a handler in a later statement never runs. Use " +
        'await Promise.all([page.waitForEvent("dialog").then((d) => d.accept()), button.click()]) ' +
        'or register page.once("dialog", (d) => d.accept()) before the action.',
    },
    schema: [],
  },
  create(context) {
    return {
      CallExpression(node) {
        const callee = node.callee;
        const waitsForDialog =
          callee.type === "MemberExpression" &&
          !callee.computed &&
          callee.property.name === "waitForEvent" &&
          node.arguments[0]?.type === "Literal" &&
          node.arguments[0].value === "dialog";
        if (!waitsForDialog) return;
        const parent = node.parent;
        const handledOnArrival =
          parent.type === "MemberExpression" &&
          parent.object === node &&
          !parent.computed &&
          parent.property.name === "then" &&
          parent.parent.type === "CallExpression" &&
          parent.parent.callee === parent;
        if (!handledOnArrival) context.report({ node, messageId: "stall" });
      },
    };
  },
};

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

  // 3. Ours: conversion bugs the gates let through on a real run.
  {
    files: ["**/*.ts"],
    plugins: { s2p: { rules: { "dialog-handled-on-arrival": dialogHandledOnArrival } } },
    rules: {
      "s2p/dialog-handled-on-arrival": "error",
    },
  },
);
