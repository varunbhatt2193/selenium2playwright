# Restart here — 2026-09-06 (after 7.2, human-in-the-loop)

## Current position

**Phase 7.2 is complete: a risky pattern suspends the run with `interrupt()`,
your answer resumes it, and the answer changes the output — proved live. Next is
7.3 (long-term memory, the Store); it has not started.**
Read [human-in-the-loop.md](human-in-the-loop.md) first, then
[short-term-memory.md](short-term-memory.md) (7.2 is built on 7.1's
checkpointer). Commit/push authorization persists. The 150-line /
one-file-at-a-time rule was removed by Varun on 2026-09-06: complete a step
when asked, then one walkthrough.

## What 7.2 built

- `risk.py`: `Risk(kind, line, snippet, count)`, the `RISKS` catalogue (three
  kinds — `dialogs`, `javascript-execution`, `shared-session` — each with a
  question and three answer options written as guidance sentences for the
  model), `detect_risks` (regex, deterministic, one question per kind),
  `question()` (interrupt payload), `resolve()`, `decision_lines()`.
- `graph.risk_review` between `intake` and `convert`: one `interrupt()` per
  unanswered risk. New state keys `ask_risks` (off by default), `risks`,
  `decisions`. `decisions` persists on the thread like `conventions` and is
  rendered into the actor *and* critic prompts by `prompts.format_decisions`.
- CLI: pauses only on a `--thread` run; `--answer KIND=ANSWER`, `--no-ask`; a
  run with no terminal prints that it used the default instead of guessing
  silently. `Risk` added to `memory.CHECKPOINT_TYPES`.
- `samples/risky/secure-area.spec.ts` — non-eval fixture for the two kinds the
  pinned suite does not contain. Of the 12 eval files only AlertsPage.ts flags.
- `scripts/demo_hitl.py`, artifacts + receipt in `out/7.2/`.
- Tests: `tests/test_risk.py` (25); 161 total.

## 7.2 sharp edges (not in gap-log; LangGraph behaviour, not our bugs)

- A **paused `invoke()` returns what that invocation wrote plus
  `__interrupt__`, and no report** — not the finished state. Loop on
  `"__interrupt__" in result`.
- **Resume values are matched to `interrupt()` calls by position.** Two
  questions in one node only stay correct because `detect_risks` is pure and
  ordered. Never make the question list depend on anything that can change
  between a pause and a resume.
- `state.next` returned `()` after a *second* interrupt while the task was
  still pending with interrupts on it. Use the reply's `__interrupt__` or
  `state.tasks[…].interrupts`.
- `interrupt()` requires a checkpointer; there is nowhere to suspend to
  without one.

## Live 7.2 demo (Sonnet actor + critic, `out/7.2/`)

Same file (`AlertsPage.ts`), two answers, one question each, both arms
**4/4 gates + critic pass, no TODOs, 30 lines different**. `handler-first`:
`page.once("dialog", …)` before the click, 2 attempts — attempt 1 failed the
lint gate (`no-floating-promises`: a synchronous handler cannot await),
attempt 2 added `void` plus a `lastDialogMessage` field the critic asked for;
11,523 actor tokens. `expect-event`:
`Promise.all([waitForEvent("dialog"), click()])`, 1 attempt, 4,702 tokens. The
model's own notes cite "Human decision 1". Four traces (two per arm — a resumed
run is a second trace) in `out/7.2/demo-receipt.json`.

## What 7.1 built

- `memory.py`: `open_checkpointer` (context manager, makes the dir, strict serde
  + `CHECKPOINT_TYPES` allowlist), `thread_config`, `thread_state`,
  `list_threads`, `strict_serializer`.
- `graph.build_graph(checkpointer=None)` — the default is the old stateless
  graph, so evals and every earlier phase are untouched.
- New `ConversionState` keys: `refinement` (input), `turn`, `conventions`,
  `baseline`. `intake` is the turn boundary; `convert` gained a third entry
  (`reflection.refinement_feedback`); `critic` sees the conventions too.
- `prompts.format_conventions` + `build_prompt(conventions=)` +
  `build_critic_prompt(conventions=)`; new critic rubric line about standing
  instructions. Prompts are byte-identical to Phase 6 when there are none.
- CLI: optional `source`, `--thread`, `--refine`, `--db`, `--list-threads`,
  remembered `--out`. `.s2p/` gitignored; dependency `langgraph-checkpoint-sqlite`.
- `scripts/demo_memory.py` — real two-turn run, artifacts + receipt in `out/7.1/`.
- Tests: `tests/test_memory.py` (16); 136 total.

## Gotcha worth remembering (not yet in gap-log)

`BaseCheckpointSaver.with_allowlist()` does nothing on its own: the default
serializer allows every type (with a deprecation warning), and an allowlist on
top of "everything" is still everything. Strict mode is normally reached via
`LANGGRAPH_STRICT_MSGPACK`, read **once at import**, so patching it in a test is
too late. `memory.strict_serializer()` asks for strict directly. A type missing
from `CHECKPOINT_TYPES` fails *quietly*: it comes back as a plain dict and the
`AttributeError` surfaces much later.

## Live 7.1 demo (thread `demo-login`, Sonnet actor + critic)

Turn 1: source path only → 3 attempts, 4/4 gates, critic revise ×3,
needs-review (attempt cap). Turn 2: `{"refinement": "Use getByTestId() …"}` and
nothing else → 2 attempts, 4/4 gates, critic **pass**, needs-review (open
TODOs). `getByTestId` applied to username/password/flash; the submit button had
no id, so the agent kept `locator("button[type='submit']")` with a
`TODO(review)` instead of inventing a test id.

## Earlier state (6.4 and 6.5), kept for reference

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

Teach theory before code in plain English. The 150-line / one-file-at-a-time
rule was removed on 2026-09-06: complete the whole step when asked, then give
one walkthrough, and let Varun review before the next step.
No agents unless asked. Frequent progress updates. Existing commit/push
authorization persists; check `gh auth status` is on `varunbhatt2193` before
pushing. Repo `/Users/varunbhatt/Downloads/Selenium2Playwright`, main, remote
`https://github.com/varunbhatt2193/selenium2playwright.git`. `.env`, `out/`,
`roadmap.md`, `plan-review.md` stay ignored; never expose credentials.
`S2P_MODEL` = actor, `S2P_CRITIC_MODEL` = critic (optional); eval CLIs
default to Opus. Use the existing `.venv` and Node toolchains. Chrome
computer-use works for LangSmith screenshots (crop the sidebar with `sips`,
offset 208 px); close tabs when done.
