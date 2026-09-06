# Phase 6.3 — one attempt vs. reflection, measured

Phase 6.3 is complete. The same pinned 12-example dataset was converted twice
per run at one clean code revision with one difference: arm A allowed **one**
conversion attempt, arm B allowed **up to three** (draft + two repairs, the
production default). Plain-English walkthrough: [reflection-ab.md](reflection-ab.md).

The A/B was run twice. The first run exposed a bug that masked the answer; the
second run, after a one-validator fix, gives the answer. Both are kept here.

## Run 2 (after the fix) — the answer

Code revision `c9459f21e9264c0446726bb54b01b9d46901c4a2`, clean worktree.

| Metric | A: one attempt | B: reflection | Delta |
| --- | --- | --- | --- |
| All four static gates passed | 12/12 (100%) | 12/12 (100%) | 0 |
| Graph report `passed` | 6/12 (50.0%) | 11/12 (91.67%) | +5 |
| Rows that used a repair lap | 0 | 2 | +2 |
| Actor model calls | 12 | 14 | +2 |
| Actor tokens | 50,860 | 63,139 | +12,279 (×1.24) |
| Critic tokens | 57,831 | 66,795 | +8,964 (×1.16) |
| Target wall-clock (sum) | 183.7 s | 211.1 s | +27.4 s (×1.15) |
| LangSmith root cost | $0.515 (12/12 rows) | $0.541 known over 11 rows, 1 missing | n/a |

**Static quality is not where reflection helps.** Every first draft that
parsed passed compile, residue, lint, and parity in both arms. The loop had
nothing to repair at that level.

**The graph-passed delta is +5, but only +1 of it is reflection.** Reflection
fired on two rows in arm B:

| Case | A: one attempt | B: reflection | Attribution |
| --- | --- | --- | --- |
| dynamic-loading-page | needs-review (critic: revise) | **passed on attempt 2** | reflection |
| alerts-page | needs-review (critic: revise) | gates + critic pass on attempt 2, but 2 TODO(review) left → needs-review | reflection did its job; a human still must look |
| iframe-page, login-page, upload-page, windows-page | needs-review (critic: revise) | passed on attempt **1** | run-to-run variance: same draft budget, the critic simply said pass this time |
| all six test files | passed | passed | never fired |

The comparison script labels those four rows `improved without repair
(variance)`. They say nothing about the loop; they say the critic's verdict on
POMs is not stable between runs.

**Every needs-review row in arm A is a page object.** All six tests passed in
one attempt in both arms. The critic asks for revisions on POMs (usually
locator choice: an accessible name invented from a CSS selector), not on tests.

## Run 1 (before the fix) — what it exposed

Code revision `653ba6df0df7a5c4331fba8ba76ef67e79f63799`, clean worktree.

| Metric | A: one attempt | B: reflection |
| --- | --- | --- |
| All four static gates passed | 11/12 | 9/12 |
| Graph report `passed` | 9/12 | 8/12 |
| Rows with **no code at all** | 1 (iframe-test) | 3 (alerts-page, login-page, windows-test) |
| Among rows with a draft: all gates passed | 11/11 | 9/9 |

Reflection looked worse only because three of its first drafts never parsed:
the model wrote `notes` as one string (or `<item>`-wrapped lines) instead of a
JSON array, Pydantic rejected the reply, and the graph's conversion-error edge
went straight to `assemble`. No validation, critic, or repair ran for those
rows. Across the 6.2 baseline and run 1 that was 5 of 36 first drafts (13.9%).
Evidence: [phase-6.3-comparison-before-fix.json](phase-6.3-comparison-before-fix.json),
[phase-6.3-delta-before-fix.jpg](phase-6.3-delta-before-fix.jpg).

**The fix** (commit `c9459f2`, gap T9): a Pydantic `mode="before"` validator on
`ConversionResult.notes` and `.todos` that wraps a string into a list (one item
per non-blank line, `<item>` tags stripped). The field description the model
sees is unchanged, so the rerun measures exactly this one tolerance change.
Result: 0 of 24 first drafts failed to parse in run 2.

## The number that replaces "perfect"

After the fix, on 12 pinned files with Opus: **12 of 12 pass all four static
checks whether or not reflection runs; the fully-passed graph report goes from
6 of 12 to 11 of 12 with reflection, of which 1 row is the repair loop itself
and 4 rows are critic variance.** Reflection costs about 24% more actor tokens,
16% more critic tokens, and 15% more wall-clock on this dataset.

## What was held fixed (run 2)

| Setting | Recorded value |
| --- | --- |
| Dataset | `selenium2playwright-v1-4920b5f319d8`, ID `33c80b1e-96bd-4b5b-a9c1-ca49d215828f` |
| Pinned dataset version | `2026-09-06T03:05:09.476354+00:00` |
| Collection SHA-256 | `4920b5f319d827a25a3d8f1a2f026c430e1fb20bdb897c6b9c3599f55b8aeb3d` |
| Model (actor and critic) | `anthropic:claude-opus-5`; 8,192 output tokens; critic effort medium |
| Evaluators | `deterministic-v1` (compile, residue, typed lint, parity) |
| Concurrency / repetitions | 1 / 1 |

| Arm | Experiment | ID | Configuration SHA-256 |
| --- | --- | --- | --- |
| A: one attempt | [`s2p-6.3-claude-opus-5-attempts1-d44da1dd`](https://smith.langchain.com/o/32ac11b4-3e72-4765-a59b-5dc1bcd32cbe/datasets/33c80b1e-96bd-4b5b-a9c1-ca49d215828f/compare?selectedSessions=d630ee69-6e27-46bb-b14a-36999db469cf) | `d630ee69-6e27-46bb-b14a-36999db469cf` | `91c37545bbd12fd8b3fa04f345d986068eff422ede8637cde8f1e687949f1b07` |
| B: reflection | [`s2p-6.3-claude-opus-5-attempts3-eda105ea`](https://smith.langchain.com/o/32ac11b4-3e72-4765-a59b-5dc1bcd32cbe/datasets/33c80b1e-96bd-4b5b-a9c1-ca49d215828f/compare?selectedSessions=d52cc925-4038-4a44-89c2-e2bc7640755c) | `d52cc925-4038-4a44-89c2-e2bc7640755c` | `e57e6aead16e1ca8bc2e2b387105b468fc7a4b2cc37734410dc5cb398d23effa` |

The two configuration hashes differ only in `max_attempts`; the comparison
checks every other key and reports `comparable: true`. Both arms have complete
local evidence and verified LangSmith readback (12 roots, 96 feedback each).

[Side-by-side view of both run-2 experiments in LangSmith](https://smith.langchain.com/o/32ac11b4-3e72-4765-a59b-5dc1bcd32cbe/datasets/33c80b1e-96bd-4b5b-a9c1-ca49d215828f/compare?selectedSessions=d630ee69-6e27-46bb-b14a-36999db469cf,d52cc925-4038-4a44-89c2-e2bc7640755c).

![LangSmith comparison of the two experiments after the fix](phase-6.3-delta.jpg)

Run 1 experiments, for the record: A `a8fead0a-b63c-4b38-ad34-18f9c5474d2e`,
B `e4b095f6-f29e-4dc6-9c47-ee211014e7be`, both at revision `653ba6d`.

## What this does not prove

- Static gates do not establish browser correctness. "Graph passed" adds the
  critic's opinion and an empty TODO ledger, not a browser run.
- One run per arm per revision. The four "variance" rows show how much the
  critic's verdict moves between runs. Sizing the reflection effect properly
  needs repeated runs (the harness supports it).
- Arm A still ran the critic, so it is not the cheapest possible pipeline.
- One cost row is missing in arm B (LangSmith reported partial tokens for that
  root), so the B cost total is unavailable; the known subtotal covers 11 rows.

## Evidence

- Tracked: [phase-6.3-comparison.json](phase-6.3-comparison.json) (run 2, every
  number above per row), [phase-6.3-comparison-before-fix.json](phase-6.3-comparison-before-fix.json)
  (run 1), [phase-6.3-delta.jpg](phase-6.3-delta.jpg), [phase-6.3-delta-before-fix.jpg](phase-6.3-delta-before-fix.jpg).
- Local, ignored: `out/6.3/ab-20260906T083121Z-73fcdb15/` (run 2) and
  `out/6.3/ab-20260906T081429Z-8a59b789/` (run 1), each with `attempts-1/`,
  `attempts-3/` (plan, journal, report, cloud readback) and `comparison.md`;
  `out/6.3/completion-offline-tests.txt`.
- Run 2's comparison was regenerated with `--compare-only` after a
  reporting-only change to `eval_compare.py` (the variance labels). No model
  was re-run and no score changed.
