# Phase 6.1 — remaining browser fixtures, theory and code walkthrough

The dataset now contains six scenarios, each represented by one Selenium POM,
one Selenium test file, and an independently authored Playwright pair. That is
12 conversion examples and eight browser tests per framework. The converter
did not generate these reference outputs. This is a curated development benchmark.

The user asked to finish 6.1 on 2026-09-05, authorizing completion of the remaining
increments without the earlier pause after each patch. The new references were
reviewed by the agent against the manifest and actual browser behavior. The
manifest records this as agent curation, not human approval. Login and alerts
retain their earlier user-review provenance. The login Selenium harness now
uses headless Chrome and guards cleanup; its assertions and golden are unchanged.

## The common pattern: preserve behavior, change the framework mechanism

A POM owns page interactions. A test owns the scenario, expected results, browser
lifetime, and temporary resources. Selenium getters return strings after explicit
waits. The Playwright goldens expose locators so assertions can retry against the
live DOM. Copying an immediate string getter into a golden would lose that useful
retry behavior. API names need not match when the golden intentionally changes
the interface: test-file examples receive that golden POM as their companion.

All test identities are preserved across source and golden. Source Mocha hooks
create Chrome and quit it after the suite. Playwright provides an isolated page
through its fixture. New Selenium files keep their setup inline so snapshotting
a test does not introduce an undeclared local helper dependency. This small amount
of repetition makes each file-conversion task explicit and portable.

## Iframe: scope the lookup and preserve access to the parent

The public `/iframe` page initializes TinyMCE from an external CDN. Its textarea
initially contains `Your content goes here.`; TinyMCE creates the actual iframe
asynchronously. The iframe's generated ID is unsuitable as a stable selector,
so both POMs use `.tox-edit-area iframe`, then `#tinymce` inside that frame.

In [Selenium IframePage](../samples/selenium-suite/pages/IframePage.ts), `open()`
navigates to the demo. `getEditorText()` waits until WebDriver can enter the frame,
waits for nonempty editor content, returns it, and restores the parent document
in `finally`. `getHeadingText()` reads the parent's `h3` using the restored driver.

The `return await body.getText()` is intentional inside `try`: the read finishes
before `finally` switches the driver back. A return of an unawaited promise here
would allow cleanup to race the operation whose result the method promises.
The finalizer covers errors during the read too; browser teardown remains owned
by the test suite if opening or entering the frame fails.

In [Playwright IframePage](../samples/playwright-golden/pages/IframePage.ts), the
constructor creates `editorBody` through `frameLocator()` and a parent `heading`
through `page`. Neither lookup changes the Page's current frame. `open()` navigates
using the base URL configured by the test runner. The golden test checks the
editor text with a 15-second CDN initialization budget, then the exact parent heading.

The two assertions distinguish iframe contents from parent contents. Reading the
parent body accidentally cannot satisfy the exact editor-text assertion. Forgetting
to restore Selenium's frame makes its parent heading lookup fail. This case does
not type, resolve the earlier TinyMCE editing failure, or cover nested frames.
See [Playwright frames](https://playwright.dev/docs/frames) and
[Selenium frames](https://www.selenium.dev/documentation/webdriver/interactions/frames/).

## Windows: wait for the new browsing context before using it

The `/windows` page contains a `Click Here` link with `target='_blank'`. The
resulting window has heading `New Window`; the original has `Opening a new window`.
Those different expectations catch assertions accidentally made on the wrong page.

In [Selenium WindowsPage](../samples/selenium-suite/pages/WindowsPage.ts), `open()`
loads the demo. `openNewWindow()` captures the existing handle set, clicks the
link, waits for an added handle, and switches to it. Set difference matters:
array position is not a reliable identity for the new window. A truthiness guard
narrows the wait's `string | false` TypeScript result before using the handle.
`getHeadingText()` waits for a visible heading in the selected window.

The Selenium test remembers the original handle before opening the child. It
checks the child's heading, closes the child in `finally`, switches back, and
checks the original. The suite's `quit()` provides final browser cleanup on failure.

In [Playwright WindowsPage](../samples/playwright-golden/pages/WindowsPage.ts),
`openNewWindow()` returns the popup `Page`. `Promise.all()` registers the popup
listener before triggering the click and awaits both operations. The test checks
the popup's heading, closes that Page in `finally`, and checks the original POM's
heading. It does not need a global window switch. See
[Playwright pages and popups](https://playwright.dev/docs/pages).

## Upload: capture the whole test dependency, including its file lifecycle

A test referring to `/Users/someone/Desktop/sample.txt` would not be reproducible
from a dataset row. Both upload tests instead create a unique temporary directory,
write `conversion-eval.txt` with harmless fixed contents, upload its absolute path,
and remove their own directory in `finally`. No separate fixture attachment is needed.

In [Selenium UploadPage](../samples/selenium-suite/pages/UploadPage.ts), `open()`
loads `/upload`; `upload(filePath)` sends the path to `#file-upload`, clicks the
submit input, and waits for the result element. `getHeadingText()` returns the
confirmation heading; `getUploadedFilename()` returns the displayed filename.

In [Playwright UploadPage](../samples/playwright-golden/pages/UploadPage.ts),
the constructor exposes `heading` and `uploadedFilename`. `upload(filePath)`
calls `setInputFiles()` and then clicks Upload. Selecting a file and submitting
the form are separate actions. The test checks `File Uploaded!` and the exact
filename using retrying locator assertions. A successful upload of the wrong
named file therefore fails. This does not verify server-side file bytes or deletion.
See [Playwright file inputs](https://playwright.dev/docs/input#upload-files).

## Dynamic loading: distinguish element existence, visibility, and text

`/dynamic_loading/2` inserts its result after a deliberate delay of about five
seconds. An immediate `findElement()` can fail because the node does not yet exist.
A fixed sleep would make every run wait that long and still fail if the page is slower.

In [Selenium DynamicLoadingPage](../samples/selenium-suite/pages/DynamicLoadingPage.ts),
`open()` navigates, `start()` clicks Start, and `getFinishedText()` waits for
`#finish h4` to exist and become visible before returning its text. Each explicit
wait has a ten-second budget, within the suite's overall 30-second timeout.

In [Playwright DynamicLoadingPage](../samples/playwright-golden/pages/DynamicLoadingPage.ts),
the constructor can create `finishedText` before the node exists: a locator is a
description of a future lookup. `start()` triggers loading. The test first retries
visibility for up to ten seconds, then checks `Hello World!`. These two assertions
preserve the source's visibility wait as well as its text expectation. The golden
therefore has one more assertion than the source, which the loss-detection parity
gate permits. See [Playwright assertions](https://playwright.dev/docs/test-assertions).

## Measured evidence and what it establishes

On 2026-09-05 local time (2026-09-06 UTC), the full maintained suites ran against
the public demo. [The machine-readable evidence](evaluation-fixture-evidence.json)
records per-test names, durations, settings, tool versions, and hashes of every
source/golden file. Source and reference browser suites ran as independent processes.

| Scenario | Browser tests | Selenium result | Playwright result | Assertions: source → golden |
|---|---:|---|---|---:|
| Login | 2 | 2 passed | 2 passed | 2 → 2 |
| Alerts | 2 | 2 passed | 2 passed | 2 → 2 |
| Iframe | 1 | 1 passed | 1 passed | 2 → 2 |
| Windows | 1 | 1 passed | 1 passed | 2 → 2 |
| Upload | 1 | 1 passed | 1 passed | 2 → 2 |
| Dynamic loading | 1 | 1 passed | 1 passed | 1 → 2 |
| Total | 8 | 8 passed | 8 passed | 11 → 12 |

Selenium reported 19.426 seconds for its suite. Playwright reported 18.661 seconds,
one worker, retries disabled, eight expected passes, zero skipped, zero unexpected,
and zero flaky results. These timings include different runner/hook scopes and
are not a framework performance comparison. One successful execution per test is
fixture evidence, not a reliability estimate across repeated runs or browsers.

The sample typecheck passed. The compile, residue, typed lint, and parity gates
passed against all 12 golden files together, with no findings. Parity compares
test identities and assertion counts; it cannot prove equivalent intent. The
earlier alerts accept/dismiss mutation still illustrates the distinction: four
static passes can coexist with a real browser assertion failure. See
[alerts-tests.md](alerts-tests.md). No integrated browser evaluator was added in 6.1.

The source browser is headless Chrome; the reference browser is Playwright's
headless Chromium. The fixture evidence records installed package versions.
Exact browser executable builds are not captured in this initial report, so do
not claim these runs pinned the same browser build. The public application and
TinyMCE CDN are external dependencies and can change independently of this repo.

## Reproduce the checks

From the repository root, run `mkdir -p samples/out/6.1/completion`, then from
`samples/`:

```sh
npm run typecheck
./node_modules/.bin/mocha --require tsx/cjs 'selenium-suite/tests/**/*.spec.ts' --reporter json > out/6.1/completion/selenium.json
./node_modules/.bin/playwright test --workers=1 --retries=0 --reporter=json --trace=on --output=out/6.1/completion/playwright > out/6.1/completion/playwright.json
```

The browser commands need external network access and permission to launch
browsers. A sandbox startup failure is a tool/environment failure and must not
be reported as a behavior failure. Raw reports and Playwright trace ZIPs remain
under ignored `samples/out/6.1/completion/`. The tracked evidence is a curated
extraction from these reports plus the four gate results; it is not regenerated
by the uploader. Rechecking changed fixtures means rerunning the relevant browser
suites and all golden gates, reviewing the results, and refreshing that evidence.

From the root, the existing validator CLIs can reproduce the gate checks:

```sh
.venv/bin/python -m selenium2playwright.validators.compile samples/playwright-golden/pages/*.ts samples/playwright-golden/tests/*.ts
.venv/bin/python -m selenium2playwright.validators.residue samples/playwright-golden/pages/*.ts samples/playwright-golden/tests/*.ts
.venv/bin/python -m selenium2playwright.validators.lint samples/playwright-golden/pages/*.ts samples/playwright-golden/tests/*.ts
.venv/bin/python -m selenium2playwright.validators.parity samples/selenium-suite samples/playwright-golden
.venv/bin/python -m unittest discover -s tests -v
```

The preflight detects source/golden edits against the recorded hashes. The report
is still curator-supplied evidence: a hash binds contents but cannot authenticate
who ran the tests or make fabricated evidence true. Do not hand-edit pass statuses
to make preflight succeed. Code review and actual execution establish that trust.
