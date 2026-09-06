# Restart here — 2026-09-06 (after the 6.5 reflection shootout)

## Current position

**Phase 6.3 is complete. 6.5 reflection-per-actor (Haiku, Sonnet, Opus
actors, Opus critic) is complete and drawn in `docs/reflection-shootout.svg`
(README). Next is 6.4 (LLM-as-judge); it has not started.** Start with
[reflection-shootout.md](reflection-shootout.md). Commit/push authorization persists. Read
[reflection-haiku-ab.md](reflection-haiku-ab.md) (plain English) and
[phase-6.5-haiku-report.md](phase-6.5-haiku-report.md) (evidence) first;
[reflection-ab.md](reflection-ab.md) / [phase-6.3-report.md](phase-6.3-report.md)
are the Opus A/B they build on.

## What the Haiku step built (commit `1c8edad`)

- `env.critic_model_name()` / `env.model_names()`: `S2P_CRITIC_MODEL`, empty
  means "same as `S2P_MODEL`"; `env.required()` covers every provider in use.
- `llm.make_model(for_critic=True)` and `llm.prepare_messages(for_critic=True)`
  resolve the critic's model; `graph.critic` uses the latter.
- `eval_plan.configuration(..., critic_model)` hashes `critic_model`;
  `build_plan(..., critic_model=)`; metadata `models` lists both.
- `run_experiment` refuses a `S2P_CRITIC_MODEL` that disagrees with the plan
  and adds `-critic-<model>` to the prefix only when it differs.
- `eval_compare` carries `critic_model` and `phase`; `run_reflection_ab.py`
  gains `--critic-model` and `--phase` (output under `out/<phase>/`).
- Tests: `tests/test_model_split.py` (7); 106 total.

## Added after the Sonnet run (same day)

- `eval_compare`: any row whose error starts with `Error code: ` (provider
  HTTP error) marks the arm non-comparable ("rerun that arm").
- `eval_shootout.py` + `scripts/render_actor_shootout.py`: SVG + markdown
  table across actors from comparison receipts; refuses non-comparable
  receipts and mixed critics. Output `docs/reflection-shootout.svg`,
  `docs/reflection-shootout-table.md`, embedded in README.
- Tests: `tests/test_eval_shootout.py` (2), provider-error test in
  `test_reflection_ab.py`; 109 total.
- Sonnet, valid run at `52256a9` (third attempt; first hit a credit failure,
  T10; second hung after the Mac slept, killed; run live experiments under
  `caffeinate -i -s`): A `5773296b-6650-4850-a5a3-8d716adc5e1e`, B
  `fedf9a3c-ac1a-4316-a8df-11fd6c94563a`; static 12/12 both; graph 11/12 →
  10/12 (login-page: critic variance then TODOs); 3 repairs; cost $0.289 →
  $0.404. Receipt `docs/phase-6.5-sonnet-comparison.json`; report
  `docs/phase-6.5-sonnet-report.md`; artifacts
  `out/6.5/ab-20260906T231129Z-98298800/`.
- `docs/reflection-shootout.md` is the one-page reading of all three actors.

## Live evidence (revision `1c8edad`, clean)

A `s2p-6.5-claude-haiku-4-5-20251001-critic-claude-opus-5-attempts1-cd1eedf0`
/ `5cbc2e15-0208-494f-9fe8-9827faa72319` / config `d84e5fae…`; B
`…-attempts3-6b251c97` / `332774ac-fe8f-4358-baa2-ae8aee0a74aa` / config
`61ea0ba1…`. All-static 9/12 → 12/12 (compile 9 → 12, lint 11 → 12); graph
passed 2/12 → 9/12; repairs used on 8 rows (5 two attempts, 3 three);
7 improved with repair, 2 variance, 3 same, 0 regressed. Actor calls 12 → 23,
actor tokens 42,510 → 95,328, critic tokens 62,374 → 119,489, wall-clock
199 → 391 s, cost $0.357 → $0.689 (complete both arms). Three-way against
6.3 run 2: Haiku+reflection (9/12) beats Opus alone (6/12), trails Opus +
reflection (11/12), costs more. Receipt `docs/phase-6.5-haiku-comparison.json`;
screenshot `docs/phase-6.5-haiku-delta.jpg`; artifacts
`out/6.5/ab-20260906T085549Z-5764b5b4/` (ignored).

Discarded: `out/6.5/discarded-dirty-worktree-ab-20260906T085156Z-1b8daf8f/`
(orphan LangSmith experiment `e931ca7e-…`), rejected by the runner because a
doc file was created in the worktree mid-run. **Do not touch the repo while
a live experiment is running.**

## Open observations for 6.4

- Critic verdict on POMs is unstable between runs (4 of 6 in 6.3, 2 of 12
  here). Calibrate the rubric judge against the goldens before trusting it.
- The critic dominates cost when the actor is cheap; a Haiku-critic arm was
  deliberately not run (two variables at once).
- LangSmith UI averages can drop rows; quote the receipts.

## Working agreement and environment

Teach theory before code in plain English; code in explained patches under
150 lines, one file at a time, unless the user asks for a step "in one go".
No agents unless asked. Frequent progress updates. Existing commit/push
authorization persists; check `gh auth status` is on `varunbhatt2193` before
pushing. Repo `/Users/varunbhatt/Downloads/Selenium2Playwright`, main, remote
`https://github.com/varunbhatt2193/selenium2playwright.git`. `.env`, `out/`,
`roadmap.md`, `plan-review.md` stay ignored; never expose credentials.
`S2P_MODEL` = actor, `S2P_CRITIC_MODEL` = critic (optional); eval CLIs
default to Opus. Use the existing `.venv` and Node toolchains. Chrome
computer-use works for LangSmith screenshots (crop the sidebar with `sips`,
offset 208 px); close tabs when done.
