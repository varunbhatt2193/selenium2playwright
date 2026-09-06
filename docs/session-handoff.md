# Restart here — 2026-09-06 (after 6.3 + T9 fix)

## Current position

**Phase 6.3 is complete, including the T9 fix and rerun. Next is 6.4
(LLM-as-judge); it has not started.** Commit/push authorization persists. Read
[reflection-ab.md](reflection-ab.md) (plain English) and
[phase-6.3-report.md](phase-6.3-report.md) (evidence for both A/B runs) first.

## What 6.3 built

- `reflection.resolve_attempt_cap`, `ConversionState.max_attempts` (1..3, default
  3) filled by `intake`, read by `route_after_critic` and `assemble`; CLI
  `--max-attempts`.
- `eval_target.conversion_target(inputs, max_attempts=...)`, cap inside the hashed
  plan configuration, `phase` label; `run_experiment` binds the plan's cap to the
  real target with `functools.partial`; names `s2p-<phase>-<model>-attempts<N>`.
- `eval_compare.py`: fairness checks, per-metric/per-case delta, "rows with a
  draft" breakdown, labels `same` / `improved` / `regressed` / `no draft in A|B` /
  `... without repair (variance)` (only a row that used a repair lap credits or
  blames reflection). `scripts/run_reflection_ab.py`: preview / `--run` /
  `--compare-only`.
- **T9 fix** (`c9459f2`): `ConversionResult.notes`/`.todos` accept a single
  string via a `mode="before"` validator; field descriptions unchanged.
- Tests: `tests/test_reflection_ab.py` (9), `tests/test_schemas.py` (4); 99 total.

## Live evidence

Run 1 (before fix) at `653ba6df0df7a5c4331fba8ba76ef67e79f63799`: A
`a8fead0a-b63c-4b38-ad34-18f9c5474d2e`, B `e4b095f6-f29e-4dc6-9c47-ee211014e7be`;
all-static 11/12 vs 9/12, graph 9/12 vs 8/12; 1 and 3 no-draft rows (parse
failure). Receipt `docs/phase-6.3-comparison-before-fix.json`.

Run 2 (after fix) at `c9459f21e9264c0446726bb54b01b9d46901c4a2`: A
`s2p-6.3-claude-opus-5-attempts1-d44da1dd` / `d630ee69-6e27-46bb-b14a-36999db469cf`
/ config `91c37545…`; B `s2p-6.3-claude-opus-5-attempts3-eda105ea` /
`d52cc925-4038-4a44-89c2-e2bc7640755c` / config `e57e6aea…`. All-static 12/12
both; graph 6/12 vs 11/12; repairs used 2 (dynamic-loading-page → passed;
alerts-page → gates+critic pass, 2 TODOs); 4 rows improved with zero repairs
(critic variance on POMs); actor +12,279 tokens, critic +8,964, wall-clock
+27 s. All six needs-review rows in A are POMs. Receipt
`docs/phase-6.3-comparison.json`; screenshot `docs/phase-6.3-delta.jpg`.
Both runs: same dataset/version/collection as 6.2; local complete; cloud verified.
Artifacts: `out/6.3/ab-20260906T083121Z-73fcdb15/` (run 2),
`out/6.3/ab-20260906T081429Z-8a59b789/` (run 1), ignored.

## Open observations for 6.4

- The critic's verdict on POMs is unstable between runs (4 of 6 POMs flipped
  revise→pass with no code change). A rubric-based judge (6.4) should be
  calibrated against the goldens before its verdicts are trusted more than this.
- LangSmith UI averages can drop rows from the denominator; quote verified
  row counts from the receipts.
- One cost row missing in run 2 arm B (partial cloud tokens); cost totals for
  that arm are unavailable, known subtotal only.

## Working agreement and environment

Teach theory before code in plain English; code in explained patches under
150 lines, one file at a time, unless the user asks for a step "in one go".
No agents unless asked. Frequent progress updates. Existing commit/push
authorization persists; check `gh auth status` is on `varunbhatt2193` before
pushing. Repo `/Users/varunbhatt/Downloads/Selenium2Playwright`, main, remote
`https://github.com/varunbhatt2193/selenium2playwright.git`. `.env`, `out/`,
`roadmap.md`, `plan-review.md` stay ignored; never expose credentials.
`S2P_MODEL` configures both graph models; eval CLIs default to Opus. Use the
existing `.venv` and Node toolchains. Chrome computer-use worked for both
screenshots (crop the sidebar with `sips`); close tabs when done.
