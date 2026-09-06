# Phase 6.1 — alerts tests and browser evidence

Status: after the walkthrough and mutation discussion, the user approved committing
and pushing the work. Both alerts test files are written and checked; the POM and
test references are now recorded as reviewed in the manifest.
These are curated fixtures and browser checks, not a LangSmith experiment.

## Theory: turn the behavior contract into an executable check

The page object performs the action. The test creates its starting conditions,
calls that action, and checks the observable result: Arrange, Act, Assert.
Both test files contain the same two cases, with one result assertion in each.
The suite and test names match so the existing parity gate can pair them.

Every test opens the page before its action. This matters because the source POM
waits for nonempty result text; text from a preceding action could otherwise
satisfy that wait. Selenium reloads the page in a shared browser session, while
Playwright provides an isolated page/context for each test. This small scenario
needs no shared cookies or other state carried from one test to the next.

The assertion strategies differ. Chai compares the string Selenium has already
read. Playwright checks a locator and retries until its expected text appears or
the assertion times out. See the official
[Playwright assertions guide](https://playwright.dev/docs/test-assertions).

## Selenium walkthrough

Read [the Selenium tests](../samples/selenium-suite/tests/alerts.spec.ts).
The file has 46 lines, including explanatory comments.

1. Imports provide browser creation, Chrome options, Chai assertions, and the
   source POM. `describe` and hook functions come from the Mocha test environment.
2. `describe` groups the two cases. Its regular `function` gives access to Mocha's
   `this.timeout(30000)`, which sets the suite's inherited test/hook timeout.
3. `before` creates one headless Chrome session and its page object. Chrome options
   live in their own variable because the installed `addArguments()` type returns
   base Chromium options; retaining the original variable preserves the Chrome type.
4. `after` quits the driver. The existence check avoids an additional cleanup error
   if setup failed before assigning a driver. It does not hide a setup failure.
5. `beforeEach` opens the alerts page, resetting the result before either action.
6. The first `it` accepts the simple alert, reads the result, and checks exact text.
7. The second `it` dismisses the confirmation and checks the exact cancellation
   result. An assertion that only checked for nonempty text would accept both branches.

Headless Chrome is still a real browser; it runs without a visible browser window.
The [Selenium Chrome guide](https://www.selenium.dev/documentation/webdriver/browsers/chrome/)
documents browser options. Setup and cleanup behavior uses Mocha's standard hooks.

## Playwright walkthrough

Read [the Playwright tests](../samples/playwright-golden/tests/alerts.spec.ts).
This independent golden has 25 lines and imports the reviewed Playwright POM.

`test.beforeEach(async ({ page }) => ...)` requests Playwright's built-in page
fixture. The runner creates and cleans up its isolated page/context; we construct
the POM and open the target page. See the official
[fixture guide](https://playwright.dev/docs/test-fixtures).

Each `test` awaits one POM action and then a `toHaveText` assertion against
`alertsPage.resultMessage`. There is no string getter in the golden: the locator
lets the assertion observe the page repeatedly. Awaiting the assertion keeps
the test open until that check succeeds or fails.

The same test names and one assertion per test give parity something concrete
to compare. Parity establishes neither correct dialog handling nor correct
expected text; it inventories the test/assertion structure.

## What we ran and observed

| Check | Maintained alerts fixtures | Isolated wrong-action copy |
|---|---|---|
| Sample TypeScript check | Passed | Not part of sample scan; scratch output is excluded |
| Golden compile/residue/lint/parity | All passed | All passed when explicitly supplied to the gates |
| Browser execution | Selenium: 2 passed; Playwright: 2 passed | Confirmation test: failed as expected |
| Dialog result | Expected results observed | Expected Cancel, received Ok |

The wrong-action copy changes only `dialog.dismiss()` to `dialog.accept()` in
the copied golden POM. Its test file and expected result stay unchanged. The test
then fails at the text assertion, demonstrating sensitivity to this particular
semantic error. The maintained golden files were never mutated.

This is a negative control: deliberately introduce a known defect and verify that
the check detects it. It does not establish that every possible conversion defect
will be detected. The simple alert's result alone still cannot distinguish which
API closed it; the confirmation result supplies the observable branch distinction.

The initial sandboxed runs could not complete browser startup: Playwright failed
at launch, and Selenium timed out in setup. Authorized runs outside the sandbox
passed against the hosted demo, with retries disabled for Playwright. These
startup failures are environment evidence, not failures of the result assertions.
The Playwright passing run took 3.3 seconds; Mocha reported about 4 seconds.
Those timings use different runner scopes and are not a framework benchmark.

The test-file snapshot also passed its local check: it contains the Selenium
test, the Playwright POM companion, and the test golden in reference outputs.
The static inventory found two tests and one assertion in each on both sides.
All 36 existing offline regression tests also passed after adding these fixtures.
That suite was rerun because its parity tests read the full samples tree.

## Reproduce the checks and inspect their artifacts

Run from `samples/`, with installed dependencies and permission to launch browsers:

```sh
npm run typecheck
./node_modules/.bin/mocha --require tsx/cjs selenium-suite/tests/alerts.spec.ts --reporter spec
./node_modules/.bin/playwright test playwright-golden/tests/alerts.spec.ts --workers=1 --retries=0 --reporter=line --trace=on --output=out/6.1/alerts-playwright
```

The isolated mutation files remain in ignored `samples/out/6.1/alerts-mutation/`.
That copy contains the POM, unchanged tests, and a config using `./tests` plus the
same base URL. To reproduce the preparation from a fresh checkout, run this from
`samples/` with Python; it changes only files inside the ignored scratch directory:

```python
from pathlib import Path

scratch = Path("out/6.1/alerts-mutation")
for relative in ("pages/AlertsPage.ts", "tests/alerts.spec.ts"):
    code = (Path("playwright-golden") / relative).read_text()
    if relative.startswith("pages/"):
        assert code.count("dialog.dismiss()") == 1
        code = code.replace("dialog.dismiss()", "dialog.accept()", 1)
    target = scratch / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(code)
config = Path("playwright.config.ts").read_text()
(scratch / "playwright.config.ts").write_text(config.replace("./playwright-golden/tests", "./tests", 1))
```

Then run the mutation; exit code 1 and the Cancel-versus-Ok assertion failure are
the expected observation, while a browser-launch failure would not validate it:

```sh
./node_modules/.bin/playwright test --config=out/6.1/alerts-mutation/playwright.config.ts --grep 'dismisses a JavaScript confirmation' --workers=1 --retries=0 --reporter=line --trace=on --output=out/6.1/alerts-mutation-results
```

Passing browser traces are under `samples/out/6.1/alerts-playwright/`, one
`trace.zip` per test. The expected failing trace is under
`samples/out/6.1/alerts-mutation-results/`. These are local Playwright traces;
LangSmith scoring and experiment reports are still Step 6.2 work.

The test golden is now approved. The next increment is the iframe POM pair.
The four remaining scenarios and dataset upload are still required
before Step 6.1 can be marked complete.
