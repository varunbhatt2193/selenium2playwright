# Step 11.1a — the twelve hard cases, as a second benchmark

The Phase 6.1 dataset measures whether the converter can handle an ordinary page
object and an ordinary test. It passes. That is not the same as being good at
this job, because the ordinary cases are the ones where a careful translation is
also an obvious one.

`plan-review.md` listed the twelve patterns an experienced SDET actually loses
sleep over — the ones where a mechanical translation **compiles, passes lint,
passes the residue scan, and silently tests something else**. This step turns
that list into a benchmark: a second sample suite, an independently authored
Playwright golden for every file, both suites green in a real browser, and
eleven rows uploaded to LangSmith.

Nothing here scores the converter yet. This step builds the ruler. Step 11.1b
runs the converter against it and publishes the failures.

## The twelve, and where each one now lives

| # | The pattern | Fixture |
|---:|---|---|
| 1 | Custom `driver.wait(async predicate, t)` polling | `pages/BasePage.ts`, `pages/DynamicControlsPage.ts` |
| 2 | `switchTo().alert()` accept/dismiss, handler ordering | *Phase 6.1 set: `AlertsPage.ts` / `alerts.spec.ts`* |
| 3 | `until.stalenessOf` / stale-element retry loops | `pages/DynamicControlsPage.ts` |
| 4 | Stateful `switchTo().frame()` / `defaultContent()` | `pages/NestedFramesPage.ts` |
| 5 | `getAllWindowHandles()` + `switchTo().window()` | *Phase 6.1 set: `WindowsPage.ts` / `windows.spec.ts`* |
| 6 | Implicit wait + `findElements().length` presence check | `pages/DynamicControlsPage.ts` |
| 7 | `findElements` loops that mutate the DOM (delete-all) | `pages/AddRemovePage.ts` |
| 8 | Action chains: hover menus, modifier clicks, drag-and-drop | `pages/HoversPage.ts` |
| 9 | `executeScript` workarounds (JS click, `scrollIntoView`) | `pages/AddRemovePage.ts` |
| 10 | BasePage wait helpers | `pages/BasePage.ts` |
| 11 | `before` shared driver + login; order-dependent tests | `tests/shared-session.spec.ts` |
| 12 | Promise-chained legacy code with no `await` | `tests/shared-session.spec.ts` |

Two of the twelve were already covered. Rather than write a second alerts page
and a second windows page for the sake of a round number, `eval_hardcases.py`
cross-references the Phase 6.1 fixtures by name in
`COVERED_BY_BASE_DATASET`, and `check_manifest` fails the build if a hard case
is claimed by *both* benchmarks or by *neither*. "Twelve" is therefore an
enforced statement, not a heading.

## What makes each of these hard

The point of a hard case is that **the gates cannot catch it**. Compile, residue,
lint, and parity all pass on the wrong answer. Only a human — or a golden — knows.

**The base class (10).** `BasePage` is 50 lines of `waitAndClick`,
`waitForPageLoad`, `waitForText`, `waitUntil`, `findAll`. Every one of those is a
hand-rolled version of a guarantee Playwright already gives, so the correct
conversion is to **delete almost the whole file**. There is no rule anywhere in
the toolchain that asks a model to delete working code, and a model that keeps
the helpers produces output that compiles, passes all four gates, and is wrong in
the way that matters: it carries the Selenium mental model into the new suite.
The golden keeps `visit()` and a ledger comment naming each removal and why.

**Implicit waits (6).** `DynamicControlsPage.open()` calls
`setTimeouts({ implicit: 5000 })`. That single line changes the meaning of every
`findElements` in the file, including `isCheckboxPresent()` — which is why the
Selenium test's absence check takes five seconds to conclude "not there". Drop
the implicit wait and translate `found.length > 0` into a plain `count() > 0` and
you get a test that passes on a fast render and fails on a slow one. The correct
answer is `await expect(locator).toHaveCount(0)`, which is patient in the same
way, for the same reason. The trap is that the wrong answer is *shorter*.

**Staleness (3).** `removeCheckbox()` holds a reference to the checkbox, clicks
Remove, and waits for that specific node to go stale. There is no Playwright
equivalent, because there is no node reference to go stale — a Locator is a
query. Converting `stalenessOf` to anything at all is usually the mistake; the
right move is to delete it and let the test assert the new state.

**The delete-all loop (7).** `findElements` returns a snapshot. Every click
removes one of its members. The Selenium source already re-queries, and the
tempting Playwright translation — `for (const b of await buttons.all())` — is a
snapshot again, so it goes stale halfway through. `while (await count() > 0)
first().click()` is the honest version.

**`executeScript` (9).** `AddRemovePage.addElements` contains two workarounds: a
`scrollIntoView` and a JavaScript click, both there because a native Selenium
click was unreliable once the row list reflowed the page. Playwright's `click()`
scrolls and refuses to fire while another element would receive the event, so
both lines should simply disappear. But `executeScript` is *sometimes* the thing
under test, which is exactly why `risk.py` raises the `javascript` question
rather than guessing. This fixture is the case where deleting is right.

**Stateful frames (4).** `enterTopFrame()` / `readMiddleFrame()` /
`returnToTop()` are three methods that only work in one order, because the driver
has exactly one current frame and they each move it. `frameLocator()` scopes a
lookup instead of moving anything, so the correct conversion deletes two of the
three methods and makes the order irrelevant — while keeping both assertions.

**Hovers (8).** A whole `actions({ bridge: true }).move({ origin }).perform()`
chain collapses into `.hover()`. Easy to get right, easy to get *nearly* right:
the caption is CSS-hidden, so a conversion that drops the hover still finds the
element and can still read its text.

**Shared login and no `await` (11, 12).** `tests/shared-session.spec.ts` is the
legacy file: one browser and one login in `before`, two tests where the second
only passes because the first ran, and **not a single `await` in the file** —
hooks and tests return promise chains, the way suites written against the old
control-flow manager read. Playwright gives every test a fresh context, so the
naive conversion adds a login to each test, both tests go green, and the order
dependence the suite actually had is gone without anyone noticing.

The golden refuses to do that. It uses `test.describe.configure({ mode: "serial" })`
with one explicitly shared page, preserving the behaviour, and carries a
`TODO(review)` saying that the durable fix is `storageState` from a global setup
project — a suite-level change a single-file conversion cannot make.

That TODO is deliberate. `plan-review.md` finding 6 warned that a dataset whose
goldens never admit uncertainty teaches the agent to guess confidently. This
benchmark now contains a row whose **correct answer flags a limitation**.

> **Honest limitation:** true Selenium v3 promise-manager code cannot run here.
> The manager was removed in v4, which is what `samples/` installs, so a file
> with genuinely implicit ordering would not pass in a browser and could not be
> browser-verified. The fixture uses returned promise chains instead: no `await`
> keyword anywhere, and the same conversion task — insert awaits without
> reordering side effects — but real v3 implicit ordering is not covered.

## The two suites

```
samples/selenium-hard-suite/       11 Selenium files — the inputs
samples/playwright-hard-golden/    11 Playwright files — the reference answers
```

Same relative paths on both sides, the same contract the Phase 6.1 pair uses, so
a row's companions resolve (`../pages/BasePage`) without any path rewriting.
`BasePage` gives the suite three levels of dependency — base → page object →
test — which is deeper than the Phase 6.1 set and is why two test rows carry
*two* companions.

```bash
cd samples
npm run test:hard          # the Selenium sources, headless Chrome
npm run test:hard-golden   # the Playwright goldens, headless Chromium
npx tsc --noEmit           # both suites, one project
```

## Measured evidence, 2026-09-08

`scripts/measure_hard_fixtures.py` runs everything and writes
[`docs/evaluation-hard-fixture-evidence.json`](evaluation-hard-fixture-evidence.json).
It is one command so that the evidence can be regenerated rather than trusted.

| what | result |
|---|---|
| Selenium sources, headless Chrome | **7 passed**, 0 failed, 21.1 s |
| Playwright goldens, headless Chromium, 1 worker, 0 retries | **7 passed**, 0 flaky, 14.7 s |
| compile / residue / lint / parity over all 11 goldens together | **4/4 pass**, no findings |
| `tsc --noEmit` across both suites | clean |
| toolchain | node v26.0.0, TypeScript 5.9.3, selenium-webdriver 4.48.0, @playwright/test 1.63.0, mocha 12.0.0 |

Every measurement is bound to the exact text it was measured against: each case
records the SHA-256 of its source and its golden. Edit either file and
`build_hard_collection` refuses to build rather than let a stale browser pass be
inherited by different code. `tests/test_eval_hardcases.py` proves that by
editing a golden in a temporary copy and asserting the failure.

## The rows

`build_hard_collection` is the Phase 6.1 preflight with this benchmark's actual
shape. Three rules are new:

- **A page object borrows its browser evidence.** `BasePage.ts` has no tests of
  its own; it declares `browser_evidence_from="dynamic-controls-test"`, and the
  builder checks that the named row really is a test row that ran. A test row
  that tries to borrow evidence is refused — it *is* the evidence.
- **Every row names the hard cases it exercises**, and those numbers land in the
  row's LangSmith metadata as `hard_cases` plus their titles. That is what makes
  a per-hard-case scorecard possible in 11.1b without re-reading this document.
- **A companion must be a declared page object in this manifest**, so a row
  cannot quietly receive context that nobody reviewed.

11 rows, 6 page objects, 5 tests, 7 browser tests per framework:

```
selenium2playwright-hard-v1-b233d4c101fd   ·   11 examples, verified by versioned readback
```

Receipt: [`docs/phase-11.1-receipt.json`](phase-11.1-receipt.json). The dataset
name carries the first 12 characters of the collection hash, so a changed fixture
produces a differently named dataset instead of silently mutating this one — the
same immutable-by-convention rule the first benchmark uses.

```bash
uv run python scripts/measure_hard_fixtures.py   # re-measure, rewrite the evidence
uv run python scripts/upload_hard_dataset.py     # local preview, no network
uv run python scripts/upload_hard_dataset.py --upload
```

## What this step deliberately does not claim

- **No converter score exists yet.** Nothing in this step ran the graph. "The
  hard cases are covered" means there are now tasks that measure them.
- **Eleven rows are eleven rows.** This is a curated development benchmark, not
  a held-out sample of real suites. It will overfit if the playbook is tuned
  against it without reserving cases; 11.1b keeps that in view.
- **The goldens are agent-authored and agent-reviewed**, recorded as exactly that
  in every row's `review_note`. Browser passes and green gates are evidence about
  behaviour and syntax, not a human's judgement that the idiom is right.
- **Real v3 promise-manager semantics are not covered** (see the box above).
- Drag-and-drop and modifier clicks are named in hard case 8 but only hover is
  exercised; the HTML5 drag-and-drop fallback remains an open gap.

## Where to look next

- [evaluation-coverage.md](evaluation-coverage.md) — the first benchmark's matrix,
  and the "reserve new scenarios before tuning" rule this step is the payoff of.
- [playbook.md](playbook.md) — the rulebook 11.1b will be allowed to change, but
  only with a green eval run behind it.
- [human-in-the-loop.md](human-in-the-loop.md) — `risk.py` and the three patterns
  that are questions rather than rules; hard cases 2, 9 and 11 are its subjects.
