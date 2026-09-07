# Phase 6.4 — LLM-as-judge: calibration, six experiments, two judges

Theory and interface: [evaluation-judge.md](evaluation-judge.md). Code:
`eval_judge.py`, `eval_calibration.py`, `eval_judge_pass.py`; scripts
`calibrate_judge.py`, `judge_experiment.py`, `compare_judges.py`. Rubric
`idiomatic-v1`, sha `0e7d219d6618…` (recorded in every feedback row). All
runs 2026-09-07 UTC on the code committed with this report; `out/6.4/` holds
the journals, `docs/phase-6.4-*.json` the receipts.

## 1. Calibration — does the judge measure what we think?

Three checks on the 12 hand-written goldens, offline against `samples/`:
goldens should score high; goldens broken on purpose (`xpath_locators`,
`value_assertions`, `sleeps`, `pom_assertions`, 24 variants where the
mutation found something to break) should score lower than their golden;
each golden judged twice should get the same score.

| Check | Opus judge | GPT-5.4 judge |
| --- | --- | --- |
| Judge calls → scored | 48 → 42 (8 recovered from the closing sentence; 6 no verdict after 3 tries) | 48 → 48, first try |
| Goldens, 24 judgements | mean 5.0, min 5 | mean 5.0, min 5 |
| Broken goldens lower than their golden | 18/18 scored | 24/24 |
| Mutation means (xpath / value asserts / sleeps / POM asserts) | 3.4 / 3.0 / 2.75 / 3.25 | 3.6 / 3.29 / 3.5 / 3.83 |
| Same file twice, 12 pairs | 12 exact | 12 exact |
| Model calls incl. retries → cost | 67 → $1.84 (first run without retry: 51 → $1.39) | 48 → $0.39 |

Receipts: [opus](phase-6.4-calibration-opus.json), [gpt54](phase-6.4-calibration-gpt54.json).
Both judges pass. The judge did not show the run-to-run variance the graph's
critic showed in 6.3/6.5: 24 of 24 repeat pairs agreed, across two judges.

## 2. The provider cut the Opus judge short (gap T11)

About two in five Opus replies ended with Anthropic `stop_reason: refusal`
in the middle of ordinary rubric prose, after 500–700 output tokens, leaving
reasoning but no score (21/51, 33/67, 26/89 calls across the three Opus
runs). Fix in `eval_judge.py`: take the raw structured reply from openevals,
recover the verdict from the mandatory closing sentence ("Thus, the score
should be: N", status `scored_from_reasoning`), retry a reply with neither up
to three times, and count what is still unscored instead of dropping it. On
your instruction the same rubric was then run with `openai:gpt-5.4`: zero
cuts in 120 calls. Details in [gap-log.md](gap-log.md#t11).

## 3. Judge scores on the six saved experiments

`evaluate(<experiment name>, evaluators=[idiomatic_playwright])` re-read the
saved outputs of the six 6.3/6.5 arms and attached judge feedback to them; the
converter did not run again (verified: 12 `idiomatic_playwright` feedback rows
on the Opus one-attempt experiment). Static and graph columns come from the
existing comparison receipts.

**Opus judge** ([receipt](phase-6.4-judge-pass-opus.json), 89 calls, 26 cut, $2.56):

| actor | attempts | all-static | graph passed | judge mean | 5 | 4 | ≤3 | unscored |
|---|---|---|---|---|---|---|---|---|
| haiku-4-5 | 1 | 9/12 | 2/12 | 3.75 | 2 | 5 | 5 | 0 |
| haiku-4-5 | 3 | 12/12 | 9/12 | 4.08 | 4 | 5 | 3 | 0 |
| sonnet-5 | 1 | 12/12 | 11/12 | 4.36 | 4 | 7 | 0 | 1 |
| sonnet-5 | 3 | 12/12 | 10/12 | 4.36 | 5 | 5 | 1 | 1 |
| opus-5 | 1 | 12/12 | 6/12 | **4.92** | 11 | 1 | 0 | 0 |
| opus-5 | 3 | 12/12 | 11/12 | 4.42 | 6 | 5 | 1 | 0 |

**GPT-5.4 judge** ([receipt](phase-6.4-judge-pass-gpt54.json), 72 calls, 0 cut, $0.61):

| actor | attempts | all-static | graph passed | judge mean | 5 | 4 | ≤3 | unscored |
|---|---|---|---|---|---|---|---|---|
| haiku-4-5 | 1 | 9/12 | 2/12 | 3.67 | 1 | 6 | 5 | 0 |
| haiku-4-5 | 3 | 12/12 | 9/12 | 3.92 | 2 | 7 | 3 | 0 |
| sonnet-5 | 1 | 12/12 | 11/12 | 4.25 | 3 | 9 | 0 | 0 |
| sonnet-5 | 3 | 12/12 | 10/12 | 4.17 | 3 | 8 | 1 | 0 |
| opus-5 | 1 | 12/12 | 6/12 | **4.58** | 7 | 5 | 0 | 0 |
| opus-5 | 3 | 12/12 | 11/12 | 4.33 | 4 | 8 | 0 | 0 |

**Two judges, same 70 rows** ([agreement](phase-6.4-judge-agreement.md)):
57 exact, 70 within one point; Opus higher on 12 rows, GPT-5.4 higher on 1.
Every disagreement but one is a 5 against a 4. Opus is the more generous
grader of real conversions and the harsher grader of synthetic breakage.

## 4. Reading

- **The judge sees what the tools cannot.** In both Haiku arms three files
  pass all four static gates and still score 3 from both judges: `By.id` ported
  one-for-one as CSS where a role or label existed, and text-returning getters
  that push tests toward extract-then-assert. Compile, residue, lint, and parity
  are blind to this; it is exactly what the rubric was written for.
- **The judge is blind to what the tools see.** Haiku one-attempt
  `iframe-page` fails compilation and still gets a 4 from both judges. By
  design: the rubric says "do not grade correctness". Neither measure replaces
  the other.
- **Reflection helped Haiku's style, not Opus's.** Haiku: 3.75 → 4.08 (Opus
  judge), 3.67 → 3.92 (GPT). Opus: 4.92 → 4.42 and 4.58 → 4.33, while the
  graph's own pass count went 6/12 → 11/12. The six Opus first drafts the
  critic sent back were near-perfect Playwright to two independent judges; the
  repaired drafts came back with `TODO(review)` commentary and locator hedges
  the critic had asked for. This is the 6.3 "critic variance" seen from
  outside: the critic was re-arguing good code, and the loop made the style a
  little worse while making the critic happier.
- **Sonnet one attempt stays the cheapest good answer.** 4.36 / 4.25 with
  11/12 graph passes and no reflection cost; reflection moved it to 4.36 / 4.17
  and cost one graph pass (the `login-page` TODOs).
- **Judge ≠ ground truth, in numbers.** Two judges agreed exactly on 81% of
  rows and within one point on 100%; both put every golden at 5 and every
  broken golden below it. That is enough to trust the *ordering* of the arms.
  It is not enough to trust a single 4-versus-5 on a single file, and the
  0.1–0.3 gap between judges says which judge is used must be printed next to
  every mean, which the receipts do.

## 5. Spend and time

Phase 6.4 live spend ≈ $6.8: Opus calibration $1.39 + $1.84, Opus judge pass
$2.56, GPT-5.4 calibration $0.39, GPT-5.4 judge pass $0.61, plus ~$0.30 of
single probe calls. GPT-5.4 averaged 1.6k tokens per verdict and 6–7 s; Opus
3.1k tokens and 8–9 s, before retries. Recommended judge setting for now:
`S2P_JUDGE_MODEL=openai:gpt-5.4`.

## 6. What this does not show

No execution evidence: the judge never ran a test. Sample size is 12 files
per arm, so a one-row change moves a mean by ~0.08. Both judges were run once
per experiment row; repeat agreement was measured on goldens only. The
Anthropic cut-off is mitigated, not explained.
