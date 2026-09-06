# Restart here — 2026-09-06 (after 6.3)

## Current position

**Phase 6.3 is complete. Next is 6.4 (LLM-as-judge); it has not started.**
The user asked for 6.3 to be finished in one go with a plain-English write-up.
Commit/push authorization persists. Read
[reflection-ab.md](reflection-ab.md) (plain English) and
[phase-6.3-report.md](phase-6.3-report.md) (evidence) first.
The 6.2 baseline and its recovery story stay in
[phase-6.2-report.md](phase-6.2-report.md) and
[evaluation-recovery.md](evaluation-recovery.md).

## What 6.3 built

- `reflection.resolve_attempt_cap`, `ConversionState.max_attempts` (1..3, default
  3) filled by `intake`, read by `route_after_critic` and `assemble`; CLI
  `--max-attempts`.
- `eval_target.conversion_target(inputs, max_attempts=...)` records the cap and
  tags traces `attempts:N`. `eval_plan.configuration/build_plan` take
  `max_attempts` and `phase`; the cap is inside the hashed configuration.
  `eval_experiment.run_experiment` binds the plan's cap to the real target with
  `functools.partial` and names experiments `s2p-<phase>-<model>-attempts<N>`.
- `eval_compare.py`: fairness checks (everything but `max_attempts` must match),
  per-metric and per-case delta, "rows with a draft" breakdown, markdown scorecard.
- `scripts/run_reflection_ab.py`: preview / `--run` / `--compare-only`.
- 9 new offline tests in `tests/test_reflection_ab.py`; 95 total pass
  (`out/6.3/completion-offline-tests.txt`).

## Live evidence

- Code revision for both arms: `653ba6df0df7a5c4331fba8ba76ef67e79f63799`, clean.
- Arm A `s2p-6.3-claude-opus-5-attempts1-d75f27de`, ID
  `a8fead0a-b63c-4b38-ad34-18f9c5474d2e`, config
  `c4865e53506125211715934d836636a8530341ff27870aa621f537598bb04cc5`.
- Arm B `s2p-6.3-claude-opus-5-attempts3-3c890967`, ID
  `e4b095f6-f29e-4dc6-9c47-ee211014e7be`, config
  `2e8e284cfac4c5df377d32f9b04e2825f87d9a6a535989008aa58730a65a8f93`.
- Same dataset/version/collection hash as 6.2. Both arms: local complete, cloud
  verified (12 roots, 96 feedback).
- Artifacts: `out/6.3/ab-20260906T081429Z-8a59b789/` (ignored); tracked receipt
  `docs/phase-6.3-comparison.json`; screenshot `docs/phase-6.3-delta.jpg`
  (LangSmith compare view, sidebar cropped).

## Results in one paragraph

All-static 11/12 (A) vs 9/12 (B); graph passed 9/12 vs 8/12. The delta is not
reflection: 1 (A) and 3 (B) first drafts failed `ConversionResult` parsing
(`notes` returned as a string) and `route_after_convert` sends a conversion
error straight to `assemble`, so those rows never saw validation, critic, or
repair. Among parsed drafts, 100% passed all four gates in both arms; reflection
fired on 2 rows in B and rescued 1 (dynamic-loading-page) for +2 actor calls and
+11,020 actor tokens; windows-page ended needs-review with one TODO after a
repair. Wall-clock equal (~174 s per arm). Critic tokens/cost totals unavailable
on both arms because no-draft rows have no critic call. The LangSmith UI
averages (0.91 / 0.82) again drop a row from the denominator; quote verified
counts. The comparison was regenerated once with `--compare-only` after a
reporting-only change to `eval_compare.py`; no scores changed.

## Top converter gap (logged as T9 in gap-log.md, not fixed)

Make the first-attempt parse failure repairable (schema coercion of `notes`,
JSON-schema method for the actor, or route attempt-1 conversion errors back to
`convert`). Any of these is a converter change: it needs its own A/B run via
`scripts/run_reflection_ab.py` before it can be claimed. Do not fold it into 6.4.

## Working agreement and environment

Teach theory before code in plain English; code in explained patches under
150 lines, one file at a time, unless the user asks for a step "in one go" as
they did here. No agents unless asked. Frequent progress updates. Existing
commit/push authorization persists; check `gh auth status` is on
`varunbhatt2193` before pushing. Repo
`/Users/varunbhatt/Downloads/Selenium2Playwright`, main, remote
`https://github.com/varunbhatt2193/selenium2playwright.git`. `.env`,
`out/`, `roadmap.md`, `plan-review.md` stay ignored; never expose credentials.
`S2P_MODEL` configures both graph models; eval CLIs default to Opus. Use the
existing `.venv` and Node toolchains. Chrome computer-use worked for the 6.3
screenshot; close tabs when done and do not compete with the user for Chrome.
