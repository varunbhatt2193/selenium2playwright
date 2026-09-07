# Prompt v1 gap log — one-shot conversion vs. the golden

*Roadmap 2.1, run 2026-09-04 on `claude-sonnet-5`, prompt = role line + `docs/playbook.md`.
Draft written by Claude from the raw diffs; Varun reviews, edits, and owns the list.
Step 2.3 grows this into the failure taxonomy that drives the validators (Phase 4).*

## Setup

| Run | Input | Output vs. golden |
|---|---|---|
| POM | `selenium-suite/pages/LoginPage.ts` | semantically identical; 3 cosmetic diffs (below) |
| Test, with converted POM as context | `tests/login.spec.ts` + `--context out/2.1/pages/LoginPage.ts` | correct code, but wrapped in a markdown fence |
| Test, no context | `tests/login.spec.ts` alone | byte-identical to the golden except the final newline |

Every run read ~2.1k tokens of playbook from the prompt cache (≈85 % of input at cache price).

## Gaps found

1. **Markdown fences leak into the output** (test-with-context run) despite the
   system prompt saying "no markdown fences". Writing that text to `.ts` would
   fail `tsc` on line 1. → *Fix owner: 2.2 structured output* — a schema field
   `code` cannot contain a fence by accident the way free text can.
2. **Output is not deterministic in shape.** Same file, two runs: one fenced,
   one clean. Anything downstream must not assume the "good" shape.
   → *2.2 (schema) + 4.x validators*: never trust, always check.
3. **No trailing newline at EOF** in all three outputs. Harmless to `tsc`,
   flagged by ESLint `eol-last` / Prettier. → *Normalize on write* (2.2) and
   let the lint gate (4.3) enforce it.
4. **Value imports where the golden uses type-only imports**:
   `import { Page, Locator }` vs. `import { type Locator, type Page }`. Both
   compile under the current sandbox `tsconfig.json`; under
   `verbatimModuleSyntax` the value form is an error. → *Playbook candidate*:
   "import `Page`/`Locator` as `type`". Decide when the sandbox config is
   frozen in 4.1.
5. **Gratuitous identifier rename**: private field `url` became `path`. Not
   wrong, but unrequested drift makes diffs noisier and review harder.
   → *Playbook candidate*: "preserve identifiers unless a rule requires a
   change (e.g. rule 20 turning a getter into a Locator field)".
6. **Formatting drift only** (one-line vs. wrapped `expect(...)` calls). Pure
   Prettier. → An exact-match eval would be wrong here; the 6.x evaluators
   must format both sides (or compare AST) before diffing.
7. **The test converted correctly without seeing the converted POM** because
   playbook rule 20 made `flashMessage` predictable. That is luck with a
   rule behind it, not a guarantee — a POM with a less obvious converted API
   would be guessed. → Suite mode (9.x) keeps the POM-first wave plan; the
   `--context` flag in `one_shot.py` is the single-file preview of it.

## What did *not* go wrong (worth recording)

- Every test name and assertion survived (parity held, rules 2 / 24).
- All waits, `Builder`, `driver.quit()`, `this.timeout` were deleted, not
  translated (rules 4–6, 12–14).
- Locators climbed the ladder to `getByLabel` / `getByRole` (rule 7) and
  `getFlashText()` became an exposed `Locator` + web-first assert (rule 20).
- `baseURL` relative path (rule 22).

---

# Failure taxonomy — what can go wrong, and which gate catches it

*Roadmap 2.3, 2026-09-04. Evidence: the 2.1 raw outputs, the 2.2 structured
outputs, and a throw-away "hard cases" probe (JS alert, iframe editor, new
window, xpath, sleep, executeScript) converted with the 2.2 pipeline. This is
M0's closing artifact and the requirements list for the Phase 4 validators.*

## Evidence gathered

| Run | `tsc --noEmit` | Runs in a browser? |
|---|---|---|
| 2.1 raw test output (markdown fence) | ❌ `TS2349` on line 1 | n/a |
| 2.2 POM + test (login) | ✅ 0 errors | ✅ 2 passed against the-internet |
| 2.2 hard-case probe (3 tests) | ✅ 0 errors | ❌ 1 of 3 failed (iframe editor) |
| mutant: wrong text inside the `page.once("dialog")` handler | ✅ | ✅ test failed as it should |

## The taxonomy

Ordered by how loud the failure is. **The quiet ones are the dangerous ones.**

### T1 · Shape failures — output is not a file
Fences, prose, truncation (max_tokens too low). Loud: `tsc` dies on line 1.
*Seen:* 2.1 fence. *Closed by:* 2.2 schema (`code` is a typed field), and a
`max_tokens` large enough for a whole file. *Gate:* compile (4.1).

### T2 · Type/compile failures — file is real but wrong
Missing imports, wrong Playwright API names, wrong fixture signatures, value
import of a type under strict settings. Loud: `tsc` reports line + code.
*Seen:* none on this sample set (Sonnet 5 + playbook produced 0 errors on all
3 files). Expected to appear with unfamiliar APIs and bigger suites.
*Gate:* compile (4.1). Feeds the critic loop directly (5.1).

### T3 · Residue — Selenium survives in the output
`selenium-webdriver` imports, `driver.` calls, `By.`/`until.` left behind,
`chai` still imported. Loud if the import is missing (tsc), **silent if
`selenium-webdriver` is still installed** in the sandbox — then it compiles.
*Seen:* none. *Gate:* residue scan (4.2), independent of the compiler.

### T4 · Async/await slips — compiles, passes wrongly
A Playwright call without `await` is a valid Promise expression; `tsc` is
happy, the assertion never runs, the test goes green. #1 conversion bug class.
*Seen:* none this time. *Gate:* lint with `no-floating-promises` (4.3).
*Gate built (4.3):* typed ESLint catches the dropped `await` and the
promise-as-boolean (`if (locator.isVisible())`). Known hole: the plugin's
`prefer-web-first-assertions` rewrites `textContent()` + `toBe`, but not the
`expect(await el.textContent()).toContain(x)` form — that one still leans on
the critic/judge until the rule (or a residue pattern) covers it.

### T5 · Parity loss — tests or assertions quietly vanish
A test dropped, renamed, or merged; an assertion "simplified" away; an
assertion moved into a callback that might not run. Silent: everything
compiles and passes.
*Seen:* the alert assertion moved into `page.once("dialog", ...)`. Count parity
held (2 → 2) and the mutant proved Playwright still fails the test, so this
instance is fine — but the pattern needs the count + name check every time.
*Gate:* parity (4.4). Also playbook rule 24, already in the prompt.
*Gate built (4.4):* the golden suite passes; deleting its invalid-login assertion
still compiles but produces `missing-assertion`, naming the test and showing the
original source assertion. Comparison is per file, suite, and test occurrence;
extra assertions in a different test cannot mask the loss. A regex probe counted
a commented-out assertion and truncated a nested callback, so this gate uses the
existing TypeScript parser. Counts do not establish semantic equivalence or that
a callback executes. See the [walkthrough and limits](parity-gate.md).

### T6 · Semantic drift — compiles, runs, does the wrong thing
The conversion is idiomatic and typed and still wrong for this page:
- `sendKeys` → `fill()` on a **contenteditable** editor (TinyMCE): Playwright
  refuses — "not an input/textarea/select or [contenteditable]". Needs
  `click()` + `keyboard.type()` / `pressSequentially()`. → *Playbook rule 15
  needs a contenteditable clause.*
- `driver.sleep(1000)` deleted (correct per rule 13) — but if the sleep was
  hiding a real timing dependency the test now flakes. Rule 13 already asks
  for a TODO when timing "genuinely matters"; the model cannot know, so this
  stays a human-review item.
- `executeScript(...)` rewritten as a locator assertion: right here, but a
  script with side effects would be lost.
**No static gate catches T6.** Only running the tests does (11.2 execution
evals on curated cases) — or a human, which is why the TODO ledger exists.

### T7 · Honesty failures — the model was confident and wrong
Invented API, or a risky mapping with no `TODO(review)`. The probe did the
right thing once (dialog handler flagged) and arguably under-flagged once
(iframe `fill` had no TODO). Silent by construction.
*Gate:* LLM-as-judge rubric (6.4) + HITL interrupt on risky patterns (7.2) +
the consolidated TODO ledger (rule 25) so a human sees every flag in one place.

### T8 · Style drift — correct but noisy
Value vs `type` imports, identifier renames (`url` → `path`), line wrapping,
absolute URLs in tests while POMs use `baseURL`. Harmless to the compiler,
costly to review diffs and exact-match evals.
*Gate:* Prettier before any diff-based eval (6.2); playbook rules for
`type` imports and "preserve identifiers"; extend rule 22 to test files.

## What this means for Phase 4

Four deterministic gates, in the order the taxonomy demands: **compile (T1,
T2) → residue (T3) → lint (T4) → parity (T5)**. T6–T8 are the reason the
gates are necessary but not sufficient — they justify the critic (5), the
judge (6.4), the execution evals (11.2), and the human in the loop (7.2).

## Addendum 2026-09-06 — measured in Phase 6.2/6.3 (T9)

### T9 · Structured-output shape failure — no code at all
The actor's reply for `ConversionResult` sometimes puts `notes` as one string
instead of a list. Pydantic rejects it, `convert` records a conversion error,
and the graph routes to `assemble` with no draft: no validation, no critic, no
repair. Seen in 5 of 36 first drafts across the 6.2 baseline and both 6.3 arms
(WindowsPage; IframeTest; AlertsPage, LoginPage, WindowsTest). It is the single
largest source of failed rows and it is invisible to the reflection loop.
**Fixed 2026-09-06 (commit `c9459f2`):** a `mode="before"` validator wraps a
string `notes`/`todos` into a list; field descriptions unchanged. Rerun of the
6.3 A/B: 0 of 24 first drafts failed to parse, 12/12 static in both arms.
*Other options considered (not needed now):* coerce a string
`notes` into a one-item list in the schema; use the JSON-schema structured-output
method for the actor as the critic already does; or route a conversion error on
attempt 1 back to `convert` while budget remains. Evidence:
[phase-6.3-report.md](phase-6.3-report.md).

## Addendum 2026-09-06 — measured in Phase 6.5 (T10)

### T10 · Provider/billing failure counted as a quality row
During the Sonnet-actor arm B the Anthropic account ran out of credits. Two
rows received `Error code: 400 … credit balance is too low`: one on attempt 3
(the earlier draft was kept) and one on attempt 1 (no draft at all). The graph
handled both as ordinary conversion errors, LangSmith readback verified the
experiment, and the comparison labelled the second row `no draft in B`, as if
the model had failed. That is infrastructure, not conversion quality.
**Fixed 2026-09-06:** `eval_compare` marks any row whose error starts with
`Error code: ` (an HTTP error from the provider SDK) as a provider error and
refuses the arm (`comparable: false`, "rerun that arm"). `eval_shootout`
refuses to draw a receipt that is not comparable. The Sonnet arm B must be
rerun after credits are restored; arm A is valid and kept.

## Addendum 2026-09-07 — measured in Phase 6.4 (T11)

### T11 · Judge reply cut short by the provider (`stop_reason: refusal`)
The 6.4 judge asks the model for a structured reply: reasoning first, then a
1–5 score. With `anthropic:claude-opus-5` as the judge, the provider ended the
tool call early with `stop_reason: "refusal"` on roughly two calls in five
(21 of 51, 33 of 67 and 26 of 89 calls across the three Opus runs). Every cut
landed mid-sentence inside ordinary rubric prose about locators and assertions,
around 500–700 output tokens in; the files under review are the-internet demo
tests with nothing sensitive in them. A cut reply has reasoning but no `score`,
so openevals raised `KeyError: 'score'` and the row was lost.
**Mitigated 2026-09-07:** the judge passes its own `output_schema` so openevals
returns the raw reply; when the score field is missing the verdict is recovered
from the model's mandatory closing sentence ("Thus, the score should be: N")
and recorded with status `scored_from_reasoning`; a reply with neither is asked
again, at most three times, and the attempt count is kept in `evaluator_info`.
Residual with Opus: 6 of 48 calibration variants and 2 of 72 judge-pass rows
still had no verdict after three attempts. The same rubric with
`openai:gpt-5.4` as the judge: 0 cuts in 48 calibration calls and 0 in the
judge pass, one attempt each, at about a fifth of the cost.
*Gate:* the judge model is a setting (`S2P_JUDGE_MODEL`), the receipt records
it with the rubric hash, and every summary counts `judge_error` and
`scored_from_reasoning` rows instead of dropping them. Until the Anthropic
cut-off is understood, the recommended judge is `openai:gpt-5.4`; a future
increment can try `method="json_schema"` structured output (which the graph's
critic already uses) to see whether text-mode replies escape the cut.
