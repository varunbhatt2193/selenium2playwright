# Phase 6.1 — alerts page objects: theory and code walkthrough

Status: the user confirmed understanding of this POM step and authorized continuing.
The POM reference is now reviewed in the manifest. The two alerts test files have
since been written, run in the browser, and approved; their walkthrough and evidence are in
[alerts-tests.md](alerts-tests.md). No LangSmith model experiment has run.

## Theory: a dialog pauses the page's JavaScript

The demo's [JavaScript Alerts page](https://the-internet.herokuapp.com/javascript_alerts)
contains buttons for an alert, confirmation, and prompt. We inspected its actual
HTML and inline JavaScript before selecting locators or describing result messages.
This increment covers the alert and confirmation; prompt input remains outside
the initial dataset's coverage.

Native browser dialogs are outside the page DOM. WebDriver provides an Alert
object with methods for handling them; see the official
[Selenium dialog guide](https://www.selenium.dev/documentation/webdriver/interactions/alerts/).
The source fixture clicks a button, waits for a dialog, and calls its action.

Playwright exposes dialogs as events. Handling must be prepared before the action
that opens the dialog. An installed listener must accept or dismiss it so the
page can continue; without a listener, Playwright automatically dismisses dialogs.
See the official [Playwright dialog guide](https://playwright.dev/docs/dialogs).

The golden deliberately handles each dialog and awaits that work. The page object
owns the action; the upcoming tests will check the resulting page state.

| Interface | Selenium source | Playwright golden |
|---|---|---|
| Constructor | Receives the test's `WebDriver` | Receives the test's `Page` |
| `open()` | Navigates and waits for the alert button | Navigates using the configured `baseURL` |
| `acceptAlert()` | Clicks, waits for the alert, accepts it | Registers dialog handling, clicks, awaits both |
| `dismissConfirm()` | Clicks, waits for the confirmation, dismisses it | Registers dismissal, clicks, awaits both |
| Result access | `getResultText()` returns a string | `resultMessage` exposes a locator for retrying assertions |

The public action names stay aligned, while result access uses the target
framework's assertion style. Goldens need to preserve behavior without requiring
every Selenium implementation detail to survive conversion.

## Selenium code, read from top to bottom

Open [the source POM](../samples/selenium-suite/pages/AlertsPage.ts).

The import brings in `By` for locating elements, `WebDriver` for the browser
interface, and `until` for explicit conditions. The URL is absolute, following
the existing Selenium sample. The two button selectors use the `onclick`
attributes we inspected, and the result uses the page's `result` ID.

The constructor stores the driver supplied by a test; it does not launch or quit
the browser. This leaves lifecycle management to test setup and teardown.

`open()` navigates and waits for the alert button to be located. That establishes
an initial page before a test performs its one dialog action.

`acceptAlert()` clicks the alert button, then waits on `until.alertIsPresent()`.
That condition returns a WebDriver Alert object. Calling `accept()` closes it
and permits the page's script to continue.

`dismissConfirm()` follows the same flow with the confirmation button, then calls
`dismiss()`. The inspected page script maps confirmation acceptance to `Ok` and
dismissal to `Cancel`, so swapping this method changes observable behavior.

`getResultText()` finds the result element and waits for non-whitespace text.
Waiting only for element existence would be insufficient: it is present but empty
on initial load. The `/\S/` regular expression means "contains a non-whitespace
character." The method then returns the current text for a Chai assertion.

This getter assumes each test calls `open()` before its action, matching the
planned test setup. It is not a general "wait until this result changes" helper:
old nonempty text could satisfy it if a caller performed multiple actions without
resetting the page. The upcoming two tests must each start with an empty result.

## Playwright code and the promise sequence

Open [the golden POM](../samples/playwright-golden/pages/AlertsPage.ts).
It was authored directly from the page behavior and Playwright API, without
invoking the conversion graph. The user has now confirmed understanding of this step.

The constructor creates locators for the inspected button labels and the result
element. `getByRole("button", { name: ..., exact: true })` expresses the visible
control to use. `resultMessage` is public so tests can use a retrying text assertion.
`open()` uses a relative URL with the existing shared configuration's `baseURL`.

The central sequence in `acceptAlert()` is:

```typescript
await Promise.all([
  this.page.waitForEvent("dialog").then((dialog) => dialog.accept()),
  this.alertButton.click(),
]);
```

1. JavaScript evaluates the array entries in order. `waitForEvent` registers a
   wait for a dialog before the click is started; it does not block here.
2. `.then(...)` specifies what to do when that dialog arrives. The callback
   returns `dialog.accept()`'s promise, so the chain includes acceptance finishing.
3. The second entry starts the click, which opens the dialog.
4. `Promise.all` completes successfully only after both the click and dialog
   handling complete. A rejection from either reaches the method's caller.

Starting a dialog wait and awaiting it before clicking would prevent the click
from starting. Registering a wait but awaiting the click before handling the
dialog can also stall. The two operations need to be allowed to progress together.
The API reference documents event waiting at
[`page.waitForEvent`](https://playwright.dev/docs/api/class-page#page-wait-for-event).

`dismissConfirm()` uses the same sequence with `dialog.dismiss()` and the other
button. These two short methods remain explicit so their differing actions are
easy to review. The locator's assertion will retry in the upcoming test; the POM
therefore has no Playwright equivalent of Selenium's one-time result getter.

## Checks, evidence, and limits

`npm run typecheck` in `samples/` passes. Its first run found an old, ignored
Phase 2.1 output containing Markdown fences under `samples/out/`. The sample
tsconfig now excludes `out/`; generated output is checked separately by the
converter validators. The old scratch files were retained.

At the POM checkpoint, the golden passed compile, residue, typed lint, and parity. POM parity
has no browser tests or assertions to compare, so that pass is not evidence of
correct dialog behavior. The new `alerts-page` snapshot captures both files and
retained `reference_review="pending"` at that point. No live model or browser
execution was used for those initial checks. Subsequent browser evidence and
the POM's recorded user review are documented in [alerts-tests.md](alerts-tests.md).

The inspected page's simple alert produces the same success text when closed;
that DOM message alone cannot establish which dialog API closed it. The
confirmation's result does distinguish acceptance from dismissal. Keep this
limit visible when reviewing the upcoming assertions and interpreting eval scores.

POM and test review are complete. The test files and their browser evidence are in
[alerts-tests.md](alerts-tests.md). The iframe POM pair is next.
