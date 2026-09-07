# Phase 6.4 — LLM-as-judge: theory and interface

This note is written before any 6.4 code, the same way
[evaluation-evaluators.md](evaluation-evaluators.md) came before the static
evaluators. Read it, change what you disagree with, then the code follows one
file at a time.

## The idea in one paragraph

So far every evaluator is a machine check: does it compile, is Selenium gone,
does lint pass, did we keep every test. Those checks are exact, but blind to
*style*. A file can pass all four and still be bad Playwright: CSS locators
where `getByRole` was possible, `expect(await el.textContent())` instead of a
retrying `expect(locator).toContainText()`, an assertion hiding in a page
object. An LLM-as-judge is a model that reads the converted file, follows a
written rubric, and gives a score with a reason. It measures the thing our
machines cannot.

## Judge is not ground truth

A judge is an opinion with a rubric. It can be wrong, and it can change its
mind between runs. We saw exactly that in 6.3 and 6.5: the internal critic
flipped its verdict on the same POM from one run to the next. So the judge is
not trusted until it passes a *calibration*:

1. **Goldens must score high.** Our 12 hand-written Playwright files are the
   best answer we have. If the judge gives one of them a 2, the rubric or the
   judge is wrong, not the golden.
2. **Broken goldens must score low.** We take a golden and break one thing on
   purpose: swap `getByLabel("Username")` for `locator("#username")`, replace a
   web-first assertion with a `textContent()` check, add a
   `waitForTimeout(2000)`, put an `expect` inside a POM. Each broken file must
   score lower than its golden. If it does not, the rubric is not measuring
   what we think.
3. **Same input, same score.** Judge every file twice. Count how often the two
   scores differ. This is the "critic variance" number we never had.

Only after that do we point the judge at real conversions.

## Judge versus critic

| | Critic (in the graph) | Judge (in evaluation) |
|---|---|---|
| Runs | during a conversion, every lap | after the fact, on saved results |
| Talks to | the actor: its notes drive the repair | nobody: it only writes a score |
| Output | a verdict plus repair instructions | a 1–5 score plus a reason |
| Sees the golden? | never | yes, as a *reference for style*, not as the only right answer |
| Prompt | playbook + review rubric | its own short rubric, kept separate on purpose |

If the judge shared the critic's prompt, a repair that satisfied the critic
would automatically satisfy the judge and we would learn nothing. Independence
is the point.

## What `openevals` gives us

`openevals` is LangChain's small library of ready-made evaluators. We use one
function, `create_llm_as_judge`. You hand it:

- a **prompt string** with three placeholders: `{inputs}` (the Selenium
  source), `{outputs}` (what the converter produced), `{reference_outputs}`
  (the golden). openevals fills them in for every dataset row.
- a **judge model** — any LangChain chat model. We pass the one from our own
  `llm.make_model()`, so the judge obeys the same `provider:model` rule as
  everything else. Nothing in our code touches a vendor SDK.
- **`choices=[1, 2, 3, 4, 5]`** — the score must be one of these numbers.
- **`use_reasoning=True`** — the model must explain before it scores.

It returns a function. Call it with `inputs=`, `outputs=`, `reference_outputs=`
and you get back `{"key": ..., "score": 4, "comment": "<the reasoning>"}`. That
is exactly the shape LangSmith's `evaluate()` wants, so it plugs in next to
our four static evaluators.

Under the hood openevals calls `model.with_structured_output(schema)` — the
same LangChain feature our graph already uses to get `ConversionResult` back
as typed data instead of free text. The judge's answer is forced into
`{reasoning: str, score: number}`. No parsing of prose.

**Dependency note (model-agnostic rule):** `openevals` 0.2.0 lists
`langchain-openai` as a hard dependency, so installing it also installs the
OpenAI SDK. We never import it and no OpenAI key is needed; the judge model is
ours. This is an install-time cost only, and it stays honest with
[the rule](../plan.md): vendor choice lives in `.env` and `llm.py`.

## Rubric draft (you edit this)

Scored 1–5. The judge sees the Selenium source, the converted file, and the
golden. It is told the golden is one good answer, not the only one.

**A. Locators** (playbook rules 7–11)
- Prefers `getByRole` / `getByLabel` / `getByPlaceholder` / `getByText` /
  `getByTestId` when the page offers them; falls to `locator(css)` only when it
  must; never XPath where a user-facing locator exists.

**B. Web-first assertions and waiting** (rules 3, 12–17)
- Asserts on locators (`await expect(locator).toBeVisible()/toContainText()`),
  not on extracted values. No `waitForTimeout`, no hand-rolled polling, no
  leftover explicit waits that Playwright's auto-waiting makes pointless.

**C. Shape** (rules 19–21)
- POM: `readonly Locator` fields set in the constructor, actions as methods,
  no assertions. Test: `@playwright/test` fixtures (`{ page }`), `beforeEach`
  for setup, no manual driver lifecycle.

**Score anchors**
- **5** Reads like the golden or better. All three areas clean.
- **4** One small slip (a CSS locator that had a role/label alternative, one
  redundant wait). Nothing a reviewer would send back.
- **3** Works but old habits show: value-based asserts, several CSS locators,
  or a wait that hides a missing web-first assertion.
- **2** Selenium thinking in Playwright syntax: extraction-then-assert
  pattern throughout, sleeps, assertions in the POM.
- **1** Not idiomatic Playwright in any meaningful way, or not the requested
  file.

The rubric scores *idiom*, not correctness: compile, residue, lint and parity
already cover correctness with exact tools. The judge is told not to
re-check those.

## Interface sketch

```python
# eval_judge.py
JUDGE_VERSION = "idiomatic-v1"

def idiomatic_playwright(inputs: dict, outputs: dict | None,
                         reference_outputs: dict | None) -> list[dict]:
    # Same two-metric shape as gate_feedback(): a numeric score and a status.
    #   {"key": "idiomatic_playwright", "score": 1..5, "comment": reasoning,
    #    "evaluator_info": {"version", "judge_model", "elapsed_seconds", ...}}
    #   {"key": "idiomatic_playwright_status", "value": "scored|no_output|judge_error"}
    # no_output -> no model call, score None, status says why.
    ...
```

- **Judge model:** new `S2P_JUDGE_MODEL`, falling back to `S2P_CRITIC_MODEL`,
  then `S2P_MODEL`. Recorded in every piece of evaluator_info so a receipt
  always says who judged.
- **Where it runs:** *not* inside `run_experiment` by default. The static
  evaluators are free; the judge costs a model call per row. Instead the judge
  runs as its own pass over an experiment that already exists in LangSmith:
  `evaluate(<experiment name>, evaluators=[idiomatic_playwright])`. LangSmith
  re-reads the saved outputs and attaches the new feedback. That means we can
  score the Haiku, Sonnet, and Opus arms from 6.3/6.5 **without paying for
  the converter again**. It gets its own receipt under `out/6.4/`.
- **Calibration script:** `scripts/calibrate_judge.py` runs the three checks
  above (goldens, broken goldens, repeat agreement) offline against
  `samples/`, no LangSmith dataset needed, and writes
  `docs/phase-6.4-calibration.json` plus a markdown table.

## Files, in order (one per review)

1. this note
2. `pyproject.toml` (`uv add openevals`) + `env.py` (`judge_model_name()`)
3. `eval_judge.py` — rubric prompt, judge factory, `idiomatic_playwright`
4. `tests/test_eval_judge.py` — fake judge model, no network
5. `scripts/calibrate_judge.py` — goldens / broken goldens / repeat run
6. `scripts/judge_experiment.py` — score existing LangSmith experiments;
   then the 6.4 report and a judge column on the shootout table

## Check yourself

1. Why does the judge get the golden but the critic never does?
2. If a broken golden scores the same as its golden, what is wrong: the
   golden, the converter, or the rubric?
3. `evaluate()` is given an experiment *name* instead of a target function.
   What does LangSmith run, and what does it skip?
4. Why do we want `choices=[1,2,3,4,5]` rather than letting the model write
   any number?

---

## What changed when the code met reality (2026-09-07)

- **Three judge files, not one.** `eval_judge.py` (the evaluator),
  `eval_calibration.py` (broken goldens + summary), `eval_judge_pass.py`
  (scoring saved experiments + cross-judge agreement). Scripts:
  `calibrate_judge.py`, `judge_experiment.py`, `compare_judges.py`.
- **`output_schema` instead of `choices`.** Same reasoning-then-score shape,
  but openevals hands back the raw reply, so a reply the provider cut short is
  handled by us (verdict recovered from the closing sentence, or retried up to
  three times) rather than raising inside the library. See gap T11.
- **Four mutations, not three.** `xpath_locators`, `value_assertions`,
  `sleeps`, `pom_assertions`. A mutation that finds nothing to break in a file
  is skipped, so 12 goldens gave 24 broken variants, not 48.
- **A judge that would not finish.** The Opus judge lost about 40% of its
  replies to a provider-side cut. On your instruction the same rubric was run
  with `openai:gpt-5.4`, which never cut. Both judges' receipts are kept.

## Calibration results

| Check | Opus judge (`phase-6.4-calibration-opus.json`) | GPT-5.4 judge (`phase-6.4-calibration-gpt54.json`) |
|---|---|---|
| Judge calls / scored | 48 / 42 (8 recovered from the closing sentence, 6 no verdict after 3 tries) | 48 / 48 (all first try) |
| 1. Goldens (24 judgements) | all 5 | all 5 |
| 2. Broken goldens lower than golden | 18 of 18 scored | 24 of 24 |
| xpath_locators / value_assertions / sleeps / pom_assertions mean | 3.4 / 3.0 / 2.75 / 3.25 | 3.6 / 3.29 / 3.5 / 3.83 |
| 3. Same file twice (12 pairs) | 12 exact | 12 exact |
| Cost | ≈ $1.84 (67 model calls incl. retries) | ≈ $0.39 (48 calls) |

Both judges pass all three checks. Opus is the harsher grader on a broken
file (means 0.2–0.75 lower), GPT-5.4 is the one that always answers. Neither
ever marked a golden below 5, and neither changed its mind on a repeat. The
"critic variance" we saw inside the graph did not appear in the judge: same
file, same score, 24 of 24 pairs across two judges.

Judge scores on the six saved experiments, and how far the two judges agree,
are in [phase-6.4-report.md](phase-6.4-report.md).

## One more check-yourself

5. Both judges gave every golden a 5, yet they disagree by up to 0.75 on the
   broken files. Which of the two facts matters more for trusting the judge
   on real conversions, and why?
