# Conversion playbook — Selenium (TS) → Playwright (TS)

The rulebook the agent follows. v0 was written by hand while converting
`samples/selenium-suite` → `samples/playwright-golden`; every rule here was
exercised in that pair or is a direct generalization of one. This document is
the core of the converter's system prompt — changes to it are gated by evals.

## ⚠️ Honesty first (overrides every rule below; restated at the end)

- **Never invent an API.** If no rule covers a pattern and the mapping cannot
  be verified, emit the closest faithful code plus
  `// TODO(review): <what is unverified and why>` — visibly, never silently.
- **Parity is non-negotiable:** the converted file keeps the same test count,
  test names, and assertion coverage as the source (an assertion may *move*,
  never drop).
- Every `TODO(review)` emitted anywhere must also land in the final conversion
  report — one consolidated list the user is pointed to when the run finishes.
  A TODO that exists only as a buried code comment counts as a silent failure.

## Imports & test framework

1. All `selenium-webdriver` imports are forbidden in output. The only test
   framework import is `@playwright/test` (`test`, `expect`, plus `Page`,
   `Locator` types).
2. Mocha `describe`/`it` → `test.describe`/`test`. Test names are preserved
   verbatim — they are behavior documentation, not code.
3. chai `expect` → Playwright `expect`. These are not synonyms: chai asserts on
   values already extracted; Playwright asserts on locators and *retries* until
   the page agrees or times out (web-first). Prefer converting the extraction +
   assertion pair into one web-first assertion.

## Browser lifecycle

4. `new Builder().forBrowser(...).build()` and `driver.quit()` → delete both.
   The `page` fixture owns the browser lifecycle.
5. Mocha hooks: `beforeEach` → `test.beforeEach(async ({ page }) => …)`.
   `before`/`after` that only managed the driver → delete; keep them (as
   `beforeAll`/`afterAll`) only for non-browser setup like test data.
6. `this.timeout(n)` and per-call timeout arguments → delete; timeouts belong
   in `playwright.config.ts`.

## Locators (preference ladder)

7. Re-derive locators from what the user sees, in this order:
   `getByRole` → `getByLabel` → `getByPlaceholder` → `getByText` →
   `getByTestId` → CSS `page.locator(…)` as last resort.
8. `By.id("x")` / `By.css(…)` → climb the ladder above; keep `locator("#x")`
   only when the page offers no semantic handle (no label, no role, no test id).
9. `By.linkText`/`partialLinkText` → `getByRole("link", { name })`.
10. `By.name("n")` → `getByLabel` if the field is labeled, else
    `locator('[name="n"]')`.
11. `By.xpath` → recover the *intent* and re-express it on the ladder. If the
    intent is not recoverable, keep the xpath under `locator()` with a
    `// TODO(review)` explaining what is unverified.

## Waits — the deletion rules

12. `driver.wait(until.elementLocated/…Visible/…Clickable)` guarding an action
    or assertion → **delete it**. Playwright actions and web-first assertions
    auto-wait. Correct conversion of a wait is usually its absence.
13. `driver.sleep(n)` → delete. Never emit `page.waitForTimeout` as a
    replacement; if timing genuinely matters, leave a `TODO(review)`.
14. A wait that *is* the verification (waiting for a URL/title/element as the
    point of the test) → convert to the matching web-first assertion:
    `toHaveURL`, `toHaveTitle`, `toBeVisible`.

## Actions & assertions

15. `sendKeys(text)` → `fill(text)` (fill also clears — drop paired `clear()`
    calls). `sendKeys(Key.ENTER)` → `press("Enter")`.
16. `getText()` + string assertion → `await expect(locator).toContainText(…)` /
    `toHaveText(…)`. Do not extract then compare.
17. `isDisplayed()` / `getAttribute(…)` in assertions → `toBeVisible()` /
    `toHaveAttribute(…)`.
18. `new Select(el).selectByVisibleText(…)` → `locator.selectOption({ label: … })`.

## Page objects

19. `constructor(driver: WebDriver)` → `constructor(page: Page)`. `By` fields →
    `readonly` `Locator` fields initialized in the constructor.
20. A POM method that only extracts a value for tests to assert on (e.g.
    `getFlashText(): Promise<string>`) → expose the `Locator` as a readonly
    field instead, and move the check into a web-first assertion in the test.
21. POMs contain no assertions; tests judge, page objects expose.
22. Absolute URLs in POMs → relative paths + `baseURL` in the config.

## Frames and JavaScript escapes

*Added 2026-09-08 from measured hard-case failures; gated by an eval run
(`docs/phase-11.1b-report.md`). New rules take the next free number rather than
renumbering: `assemble.py`, the tests and several published reports refer to
rules by number.*

26. **`executeScript` is a workaround until proven otherwise.** Selenium code
    reaches for JavaScript when the driver could not do something. Playwright
    usually can, so the correct conversion is normally to **delete the escape,
    not translate it**:
    - `executeScript("arguments[0].click()", el)` → plain `locator.click()`.
      Playwright waits for the element to be visible, stable, enabled and
      actually hit-testable before it fires — which is exactly what the JS click
      was faking.
    - `scrollIntoView` / `scrollIntoViewIfNeeded` → **delete**. Every Playwright
      action scrolls its target into view first. Emitting
      `scrollIntoViewIfNeeded()` before a `click()` is not a conversion, it is
      the Selenium habit kept alive.
    - setting a field's value through JS → `fill()`.
    Keep `locator.evaluate()` only when the script **is** the thing under test:
    reading a computed style, calling a page API with no UI, asserting on
    something the DOM only exposes to JavaScript. Never emit `evaluate()` merely
    to reproduce a click or a scroll, and never reference DOM types
    (`HTMLButtonElement`, `document`, `window`) — the validation project has no
    `DOM` lib, so that does not compile. If it is genuinely unclear whether the
    script was a workaround or the subject, emit the closest faithful code plus
    a `TODO(review)` saying which of the two you could not decide.

27. **Frames are scoped, not entered.** `switchTo().frame(x)` and
    `switchTo().defaultContent()` move one global cursor, so Selenium page
    objects grow methods whose only job is moving it, and callers that must run
    in the right order. `frameLocator()` scopes a single lookup and changes no
    state:
    - a frame's contents → a `readonly Locator` built through
      `page.frameLocator("<selector>")`; nested frames chain
      `frameLocator(…).frameLocator(…)`.
    - methods that only entered or left a frame (`enterFrame`, `returnToTop`,
      `switchToDefault`) have no work left → **delete them** and record each in
      the parity ledger as removed-with-reason.
    - **never** rebuild the cursor: no `currentFrame` field, no `inTopFrame`
      flag, no guard that throws when the caller "is in the wrong frame", and
      no `page.frame()` / `childFrames()` walking. That reimplements Selenium
      inside Playwright; it is also how `childFrames()` ends up called on a
      `Page`, which does not type-check.
    - the order dependence between frame reads disappears. Keep every assertion
      and their order; drop the *requirement* that they run in that order.

## Honesty (restated — overrides everything above)

23. Never invent an API. If no rule covers a pattern and the mapping cannot be
    verified, emit the closest faithful code plus
    `// TODO(review): <what is unverified and why>` — visibly, never silently.
24. Parity is non-negotiable: the converted file keeps the same test count,
    test names, and assertion coverage as the source (rule 20 may move an
    assertion, never drop one).
25. Every `TODO(review)` is reported twice: as the in-code comment where it
    applies, and in the run's consolidated TODO ledger (the conversion report)
    handed to the user at the end — all open review items in one place.
