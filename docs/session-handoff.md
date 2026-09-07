# Restart here — 2026-09-06 (after 8.1, the `s2p` CLI)

## Current position

**Phase 7 is complete and Phase 8 has started. 8.1 moved the whole front end
out of `graph.py` into a Typer app: `s2p convert` with a rich scorecard and a
before/after diff, plus `s2p remember / memories / forget / threads`. Next is
Phase 8.2 (runtime `--model`, `--max-iterations`, `--json` through a config
schema); it has not started.**
Read [cli.md](cli.md) first, then [long-term-memory.md](long-term-memory.md),
[human-in-the-loop.md](human-in-the-loop.md) and
[short-term-memory.md](short-term-memory.md). Commit/push authorization
persists. The 150-line / one-file-at-a-time rule was removed by Varun on
2026-09-06: complete a step when asked, then one walkthrough.

## What 8.1 built

- `cli.py` (483 lines) — the only front end. Five commands: `convert`,
  `remember`, `memories`, `forget`, `threads`. `[project.scripts]` now installs
  `s2p`; `python -m selenium2playwright.graph` is gone.
- `graph.py` 758 → 448 lines: no `main()`, no argparse, no `print()`. Its
  reporting helpers moved to `cli.py` and became rich renderings.
- Scorecard `Table` (compile/residue/lint/parity + critic + open-TODO count),
  verdict and reason on a plain soft-wrapped line above it, findings and fixes
  printed in full underneath, then a `Panel(Syntax(..., "diff"))` before/after —
  baselined on the *previous turn* when there is one, else the source.
- `one_shot.format_usage()` split out of `report_usage()` so both surfaces
  share the token line.
- `tests/test_cli.py` (12); 7 existing test files repointed from `graph.main`
  to `cli.run`, and `graph.make_embeddings` patches became `cli.make_embeddings`.
  **203 offline tests pass.**
- Live check: `uv run s2p convert samples/selenium-suite/pages/LoginPage.ts
  --out out/8.1/pages/LoginPage.ts` — Sonnet actor + critic, 2/3 attempts,
  4/4 gates + critic PASS, exit 1 on three honest locator TODOs.

## 8.1 sharp edges

- **rich parses `[...]` as markup.** Anything dynamic goes through `say()`,
  which builds a `rich.text.Text`; a test pins `[data-testid]` surviving.
- **Click always exits by raising `SystemExit`, even on success.** `cli.run()`
  catches it and returns the code — that is the seam every test uses, and it is
  why usage-error tests assert `code == 2` instead of `assertRaises`.
- **A passing gate can still carry findings** (lint warnings): the detail cell
  says `1 finding(s)` next to PASS, never "clean".
- **Table rows are not sentences.** Assertions match the row line
  (`compile[^\n]*PASS`), and the row helper filters on the box character so the
  prose verdict line mentioning "critic" is not mistaken for the critic row.

## What 7.3 built

- `store.py`: `Memory(key, text, score, created)`, `open_store` (SqliteStore +
  vector index; records which embeddings model built the file and refuses to
  open it with another), `remember`/`forget`/`memories`/`recall`, and
  `recall_query` — a distilled **profile** of the file (kind, name, class, test
  titles, methods, locator strategies, element ids, camelCase split into words),
  not the file itself.
- `graph.recall` between `intake` and `risk_review`, given the store by
  `compile(store=…)`. New state: `user_id`, `remember`, `recalled`,
  `memory_count`. A preference given this run is always sent; older ones must
  clear `MIN_SCORE`; anything already standing on the thread is excluded.
- `prompts.format_remembered` + a REMEMBERED PREFERENCES block ahead of
  conventions and decisions, to actor *and* critic, plus a rubric line saying an
  ignored preference is not a defect.
- Embeddings: `env.embeddings_name()` / `llm.make_embeddings()` /
  `llm.embedding_dims()`, default **`openai:text-embedding-3-small`**,
  `S2P_EMBEDDINGS=off` supported, Voyage a one-line swap. `langchain-openai` is
  now a direct dependency. `Memory` added to `memory.CHECKPOINT_TYPES`.
- CLI: `--remember`, `--memories`, `--forget`, `--user`, `--no-recall`,
  `--memory-db` (separate file from `--db`; the store outlives threads).
  *(8.1 turned the first three into `s2p remember` / `memories` / `forget`;
  `--remember`, `--user`, `--no-recall` and `--memory-db` stayed on `convert`.)*
- `scripts/calibrate_recall.py`, `scripts/demo_store.py`, artifacts in `out/7.3/`.
- Tests: `tests/test_store.py` (30); 191 total.

## 7.3 sharp edges (LangGraph/library behaviour, not our bugs)

- **`SqliteStore` reads `text_fields`, not the documented `fields`.** Give it
  only `fields` and it silently embeds `"$"` — the whole JSON document — so each
  memory's `source` path lands in its own vector and the same sentence scores
  differently depending on where it was written (0.3238 vs 0.3954, observed).
  `open_store` passes both keys.
- **`store: BaseStore | None` silently disables injection.** LangGraph matches
  the parameter by name *and by the literal text of the annotation*, against a
  list holding `"BaseStore"` / `"Optional[BaseStore]"` only. With
  `from __future__ import annotations` the modern union spelling matches nothing:
  the node runs, `store` is always None, nothing is ever recalled, no error.
- **A memory written to an unindexed store can never be found again** (vectors
  are computed on write). `--remember` therefore hard-fails when embeddings
  cannot be loaded; reading degrades to recency and says so.
- **`SqliteStore.setup()` needs `isolation_level=None`** or its migrations raise
  "cannot start a transaction within a transaction".

## Recall calibration (measured, `out/7.3/recall-calibration.json`)

The naive "paste the head of the file" query separates nothing: lowest genuinely
applicable 0.2962 sits *below* the loudest unrelated 0.3044. The profile query
fixes the ranking (naming wins on page objects, fixtures on specs, noise at the
bottom). The bands still overlap, so the verdict is a **shortlist** check, not a
threshold hunt: at `MIN_SCORE = 0.29`, cap 3, the six sample files recall
**11 of 15 applicable and 0 of 18 unrelated**. Every miss is one vaguely worded
memory: prefixing the same rule with "In test specs," moved it 0.281 → 0.320.
Re-run the script after changing the embeddings model or the query.

## Live 7.3 demo (Sonnet actor + critic, `out/7.3/`)

Taught while converting `pages/LoginPage.ts` on thread `monday`, applied by
itself on `tests/upload.spec.ts` in fresh thread `wednesday` with nothing typed:
`test.step` blocks, **25 lines** different from the storeless control, all three
arms **4/4 gates + critic pass in 1 attempt**, +394 actor tokens for the recall.
Three of four memories correctly stayed behind (the vague twin, a page object
rule, and the CI/S3 noise). Receipt `out/7.3/demo-receipt.json`.

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
- CLI: optional `source`, `--thread`, `--refine`, `--db`, `--list-threads`
  *(8.1: now `s2p threads`)*, remembered `--out`. `.s2p/` gitignored; dependency `langgraph-checkpoint-sqlite`.
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
`S2P_MODEL` = actor, `S2P_CRITIC_MODEL` = critic (optional),
`S2P_EMBEDDINGS` = recall (default `openai:text-embedding-3-small`, `off`
supported); eval CLIs default to Opus. Use the existing `.venv` and Node toolchains. Chrome
computer-use works for LangSmith screenshots (crop the sidebar with `sips`,
offset 208 px); close tabs when done.
