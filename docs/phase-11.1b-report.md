# Step 11.1b — scoring the twelve hard cases, and changing the playbook because of it

Step 11.1a built the ruler: eleven conversion tasks covering the twelve SDET
patterns where a mechanical translation compiles, passes lint, passes residue,
and silently tests something else. This step used it, published the failures,
and then changed the rulebook — with each change measured rather than argued.

**Headline: graph-passed rows went 6/11 → 9/11 across two eval-gated playbook
edits, at a total cost of $1.57 for four runs.** The pass counts are the weakest
evidence in this document. The strongest is that the converted code changed in
exactly the way each rule predicted, which you can read below.

## ⚠️ Read this before quoting any number

**This ran on OpenAI `gpt-5.4`, not Claude Opus.** On 2026-09-08 the project's
Anthropic account returned, for every model:

```
400 invalid_request_error: You have reached your specified API usage limits.
You will regain access on 2026-10-01 at 00:00 UTC.
```

Account-wide, not model-specific — a one-token probe on `claude-sonnet-5` gives
the same error, and the live deployment at `s2p.fly.dev` converted nothing while
it lasted. Varun chose Opus for this step and then, once blocked, chose to
proceed on OpenAI tokens rather than wait. So:

> **These numbers are not comparable to the Opus and Sonnet figures in
> [phase-6.4-report.md](phase-6.4-report.md) or
> [reflection-shootout.md](reflection-shootout.md).** Different provider,
> different model. The *playbook* conclusions carry over; the *scores* do not.

**And a single run is not a measurement.** See the next section — it is the most
important finding here.

## The finding that shapes everything else: two identical runs disagree

Two baseline runs, same model, same dataset version, same code revision, same
attempt budget, nothing changed between them:

| | all four gates | graph passed |
|---|---|---|
| first baseline | 9/11 | 7/11 |
| second baseline (arm A) | **11/11** | **6/11** |

Four of eleven rows changed outcome between them. `add-remove-page` and
`nested-frames-page` went from a compile failure to passing all four gates;
`dynamic-controls-page` and `shared-session-test` went the other way. Temperature
is not set and these models are not deterministic.

**So an eleven-row, one-run-per-arm A/B cannot attribute a one- or two-row change
to a prompt edit.** Everything below is reported with that in mind: the pass
counts are context, and the argument rests on reading the generated code.

What *was* stable across both baselines was the diagnosis. Three page objects
never reached `passed` in either run, and both runs failed them the same way.

## The baseline scorecard, failures included

Arm A, `gpt-5.4` actor and critic, 3 attempts, 11 rows: **11/11 all four gates,
6/11 graph-passed**, $0.394, 138 s.

Five of the six `needs-review` rows are page objects. That is the shape of the
result: the *test* files convert cleanly, and the *judgement* lives in the page
objects, which is exactly where hard cases live.

| Case | Hard cases | Outcome | What the model actually did |
|---|---|---|---|
| `add-remove-page` | 7, 9 | needs-review, 2 attempts | Kept `scrollIntoViewIfNeeded()` before every click, with a TODO defending it. The delete-all loop was correct. |
| `nested-frames-page` | 4 | needs-review, 3 attempts | Rebuilt Selenium's single frame cursor: an `inTopFrame` flag, a `requireTopFrame()` guard that **throws**, and `enterTopFrame`/`returnToTop` preserved. In the earlier run it used `page.frame()` and `childFrames()`, which does not even type-check on `Page`. |
| `base-page` | 10, 1 | needs-review, 3 attempts | Kept all five wait helpers, wrapped `waitUntil` in `expect.poll` — putting a test assertion inside a page object — and the critic attacked it for exactly that. |
| `dynamic-controls-page` | 3, 6, 1 | needs-review, 3 attempts | Reached for `page.waitForFunction` with a `document` / `HTMLInputElement` body. |
| `shared-session-test` | 11, 12 | needs-review, 2 attempts | Correct serial-mode conversion; the critic wanted more. |

Two of those are the "preserve the Selenium machinery" failure in its purest
form. **The playbook had no rule about `executeScript` and no rule about
frames** — twenty-five rules covering locators, waits, actions and page objects,
and nothing at all for the two patterns that produced these outputs.

## Iteration 1 — two new rules, then measured

[Rule 26](playbook.md) — *`executeScript` is a workaround until proven
otherwise*: delete the JS click and the scroll rather than translating them;
keep `evaluate()` only when the script is the thing under test.

[Rule 27](playbook.md) — *frames are scoped, not entered*: `frameLocator()`
changes no state, so methods whose only job was moving the cursor get deleted
with a ledger entry — no `currentFrame` field, no flag, no throwing guard.

Arm B: **10/11 gates, 8/11 graph-passed**, $0.344. Two rows improved, one
regressed.

`add-remove-page` before and after is the cleanest evidence in this report:

```diff
  async addElements(count: number): Promise<void> {
    for (let index = 0; index < count; index += 1) {
-     await this.addButton.scrollIntoViewIfNeeded();
-     // TODO(review): Source used scrollIntoView plus JavaScript click because…
      await this.addButton.click();
    }
  }
```

`passed` on the first attempt, no TODO, no workaround. And `nested-frames-page`
lost its cursor entirely — two `frameLocator` chains as `readonly Locator`
fields, `enterTopFrame` and `returnToTop` gone, which is the golden's own shape.

## Iteration 2 — the regression was the same disease through another door

`dynamic-controls-page` regressed to a compile failure:

```
pages/DynamicControlsPage.ts:38:23 TS2584 Cannot find name 'document'.
pages/DynamicControlsPage.ts:39:38 TS2304 Cannot find name 'HTMLInputElement'.
```

Rule 26 named `evaluate()` and DOM types in the same breath, so it never reached
a callback handed to `page.waitForFunction`. The rule was right and incomplete.

Rule 26 now covers **every** browser-side callback — `evaluate`,
`evaluateHandle`, `waitForFunction`, `$$eval` — says why (the validation project's
`lib` is `ES2022` with no `DOM`), and points at the Playwright APIs that express
the same intent, including `waitFor({ state })` for the polling case that
produced this output. That completes one class of defect rather than fitting a
rule to one fixture.

Arm C: **10/11 gates, 9/11 graph-passed**, $0.368. The DOM-type failure is gone —
the model now writes `await expect(this.textField).toBeEnabled()`, which is
exactly what the rule asks for — and `nested-frames-page` passed on attempt 1.

## Per hard case, arm A → arm C

Groups overlap on purpose: one row exercises several patterns, so these counts do
not sum to eleven and a single row moving shows up in every group it belongs to.

| # | Pattern | Rows | Graph passed A → C | Tuned for? |
|---:|---|---:|---|---|
| 1 | Custom `driver.wait` polling | 3 | 1 → 1 | no |
| 3 | `stalenessOf` retry loops | 2 | 1 → 1 | no |
| 4 | Stateful frame switching | 2 | 1 → **2** | **yes (rule 27)** |
| 6 | Implicit wait + `findElements().length` | 2 | 1 → 1 | no |
| 7 | Delete-all loop that mutates the DOM | 2 | 1 → **2** | no |
| 8 | Action chains | 2 | 2 → 2 | no |
| 9 | `executeScript` workarounds | 2 | 1 → **2** | **yes (rule 26)** |
| 10 | BasePage wait helpers | 2 | 1 → 1 | **reserved** |
| 11 | Shared `before` login | 2 | 1 → **2** | **reserved** |
| 12 | Promise-chained, no `await` | 1 | 0 → **1** | no |

**Hard cases 10 and 11 were reserved from the tuning loop** — no rule was written
for either. 10 did not move; 11 improved, which is more likely run-to-run
variance than generalization, and is reported as such. The reservation is
recorded in `phase-11.1b-comparison.json` and the comparison refuses to describe
a reserved case as tuned for.

## What is still unsolved

**Hard case 10 — the BasePage — failed in all four runs**, every time at the
full three attempts. The model will not delete the wait helpers. It converts
them: `waitForText` keeps returning a string, `waitUntil` becomes
`expect.poll(...).toBe(true)`, `findAll` becomes `elementHandles()`. The critic
then correctly objects that a page object now contains a test assertion, the
repair loop rewrites it into a different shape with the same problem, and the
run ends at `needs-review` with honest TODOs.

The honest reading: **this file cannot be converted well in isolation.** Every
TODO it produced says the same thing — *"call sites were not provided"*. Deleting
`waitAndClick` is only correct if you can also fix its callers, and a single-file
conversion cannot. That is a limit of the task shape, not a missing playbook
rule, and writing a rule to force deletion here would be fitting the prompt to a
fixture. Left open deliberately; suite mode (Phase 9) is where it belongs.

**One compile failure survives in arm C**, and it is a different bug each run —
this time an invented matcher, `expect(...).toBeDetached()`, which does not exist.
The playbook already forbids inventing APIs (rule 23). A failure the rulebook
already covers does not get a new rule; it gets recorded.

## Reproducing this

```bash
uv run python scripts/run_eval_experiment.py --benchmark hard --model openai:gpt-5.4 --run
uv run python scripts/compare_prompt_ab.py <arm-a-dir> <arm-b-dir> --reserved 10 --reserved 11
```

The comparison refuses to call itself comparable unless every configuration key
matches except the file hashes, and among those only `docs/playbook.md` moved —
so "we changed the prompt and the score went up" is a checkable claim rather than
a story. A source edit, a different attempt budget, a different model, an
unverified cloud readback, or no file change at all each disqualify it.

| Run | Gates | Graph passed | Cost | Wall |
|---|---|---|---|---|
| first baseline (pre-tooling probe) | 9/11 | 7/11 | $0.466 | 167 s |
| **A — baseline** | 11/11 | 6/11 | $0.394 | 138 s |
| **B — rules 26 + 27** | 10/11 | 8/11 | $0.344 | 114 s |
| **C — rule 26 completed** | 10/11 | **9/11** | $0.368 | 121 s |

Total: **$1.57**. Every run cloud-verified by versioned readback; every arm's
local evidence complete.

## Limits, stated plainly

- Eleven rows, one run per arm, on a curated development benchmark. Not a
  held-out sample of real suites, and not a claim about any Selenium suite.
- Static gates do not establish browser correctness. Nothing here was executed
  in a browser; that is Step 11.2.
- The goldens are agent-authored and agent-reviewed, recorded as such in every
  row.
- The model is OpenAI `gpt-5.4` because Anthropic access was unavailable. The
  published headline numbers for this project remain the Opus/Sonnet ones from
  Phase 6; these live beside them, not in place of them.
- Two rules were written from observed failures on this benchmark. Hard cases 10
  and 11 were reserved, and both rules describe classes of defect rather than
  fixtures — but with eleven rows, overfitting is a live risk and re-measuring on
  a fresh set is the only real defence.

## Where to look next

- [hard-cases.md](hard-cases.md) — what the twelve are and how the fixtures were
  built and verified.
- [playbook.md](playbook.md) — rules 26 and 27, and the honesty block they never
  override.
- [phase-11.1b-comparison.md](phase-11.1b-comparison.md) — the full A → C
  scorecard, machine-checked.
