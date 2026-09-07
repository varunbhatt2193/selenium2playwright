# Restart here — 2026-09-07 (after 9.3, M4 shipped)

## Current position

**Phases 0–9 are complete. 🏁 M4 — the wow demo — shipped at 9.3: `s2p suite
<folder> --out <dir>` scans a Selenium suite, converts every file through the
same graph `s2p convert` uses (waves of independent files in parallel, one
LangSmith trace), then compiles the finished tree as one project and writes a
`conversion-report.md` next to the code. Next is Phase 10 — deploy, playground,
monitor: 10.1 is `langgraph.json` + `langgraph dev` + poking the graph in
Studio. It has not started.**

Read [suite-report.md](suite-report.md), [suite-fanout.md](suite-fanout.md) and
[suite-scan.md](suite-scan.md) first — that is all of Phase 9 — then
[cli.md](cli.md) and [config.md](config.md), then
[long-term-memory.md](long-term-memory.md),
[human-in-the-loop.md](human-in-the-loop.md) and
[short-term-memory.md](short-term-memory.md).
[phase-9.3-report.md](phase-9.3-report.md) is the live artifact the whole
milestone builds toward — read it to see what the tool actually hands a person.

Commit/push authorization persists. The 150-line / one-file-at-a-time rule was
removed by Varun on 2026-09-06: complete the whole step when asked, then one
plain-English walkthrough with check-yourself questions, then wait for his
review. **Always end a turn that hands control back with an explicit "waiting on
you" line** (asked 2026-09-06 — a pause must never be implied).

**301 offline tests pass** (`uv run python -m unittest discover -s tests`, ~151 s
— not `-t .`, and pytest is not installed). The suite is terminal-width
independent from 40 to 200 columns as of 9.3.

## What 9.3 built (`assemble.py`, the last step of M4)

- **The argument.** Every per-file gate verdict is a *local* claim: this file
  compiled on its own, against the companions it happened to import. Two files
  that each compile perfectly can refuse to compile together, and nothing in the
  project could see it. So the suite graph's `finish` node now compiles the
  **delivered tree as one project** (`compile_tree` → the pinned
  `tsc --noEmit` over all of `out_root`), and **exit 0 requires both**:
  `not failed and built.compiles`. A compile that could not run is
  `compiles=False` — unknown is never green.
- **Parity ledger.** `sandbox/members.cjs` is a second parse-only sandbox script
  (never imports or runs submitted code, same rule as `parity.cjs`) reporting
  each file's public surface: non-private class members, `public` constructor
  parameter properties, top-level exported functions and constants. Source names
  are matched to converted ones as kept / renamed / removed; a removal's reason
  is **quoted verbatim** from the model's own `notes`/`todos` when one of them
  names the member, else the report says **no reason given**. `FileOutcome`
  gained `notes` for exactly this. Test identities come free from a second call
  to the existing `parity.cjs`.
- **Rename detection is the one guess, and it is deliberately timid.** `stem()`
  strips accessor prefixes (get/set/is/waitFor/verify…) and type-ish suffixes
  (Text/Value/Element/Locator); containment of one stem in the other scores 1.0,
  otherwise `SequenceMatcher`, cutoff `RENAME_RATIO = 0.7`. So
  `getFlashText → flashMessage` is a rename and `open → goto` is a removal plus
  an addition. **A wrong rename hides a loss; a missed one is only noise.**
  Members pair only *inside the same class* (class renames are matched first, so
  `LoginPage → LoginPageObject` is not a bloodbath), and members and tests are
  matched in **separate pools** — a lost test can never be explained away as a
  renamed method.
- **Consolidated TODO(review) ledger** (playbook rule 25): read from *both* the
  comments in the written code (with line numbers, whole comment blocks) and the
  `todos` each conversion reported, then merged by **containment**, not equality.
- `s2p.suite-report/v1` is a strict **superset** of 9.2's `s2p.suite-run/v1` —
  same keys, new schema name, plus `tree` / `scorecard` / `parity` / `todos`.
  Markdown goes to `<out>/conversion-report.md`; `--report FILE` moves it.
- `tests/test_assemble.py` (26). The one to read first builds two files that
  each compile perfectly alone and cannot compile together; a CLI test proves
  that turns two green rows into exit 1.
- **Live 2026-09-07**, all 12 sample files, `--parallel 4`, Sonnet: 9 passed /
  3 needs-review, exit 1, **83.1 s**, the tree compiles, **20 public names kept,
  8 renamed, 0 removed** — all eight the same idiom shift
  (`getResultText → resultMessage`). Two distinct TODOs, one of them reported by
  *both* `tests/login.spec.ts` and `pages/LoginPage.ts` and collapsed to one
  line. Trace `01a07aa0-d597-75e1-bdfb-886593881b7e`: one trace, 557 runs, 32
  LLM spans; `--parallel 4` is visible in it (first four branches start within
  17 ms, the fifth 16 ms after the first finishes), wave 2 starts 28 ms after
  wave 1's slowest, and the whole `finish` assembly costs **0.63 s** of the 83.
  Report committed verbatim as `docs/phase-9.3-report.md`.

## 9.3 sharp edges

- **`members.cjs` uses `parity.cjs`'s stdin protocol**: a *list* of `{path:
  source}` maps in, the same list of inventories out, so one node process
  inventories both sides. Passing a single map made `ts.createSourceFile`
  receive an object and die inside the scanner.
- **A wrapped `// TODO(review):` comment only had its first line captured**, so
  it never matched the full sentence the model reported and one task showed up
  as two. Fixed with `todo_blocks()` (consume continuation comment lines) plus
  the containment merge. **No test caught this — only reading the live output
  did.** Read the real artifact before believing the test suite.
- **Three pre-existing width-dependent failures in `test_cli.py`** surfaced at
  COLUMNS=40/60: fixed with `test_store`'s `unwrapped()` helper and by pinning
  `cli.console.width = 100` in `CliHarness.setUp`.
- **Assembly is a graph node, not CLI code.** The graph produced the tree, so
  the graph says whether the tree holds together — and the whole-tree compile
  lands in the trace beside the conversions that made it necessary.

## What 9.2 built (`suite_graph.py` — Send, reducers, subgraphs)

- Shape: `START → plan → next_wave --dispatch--> [convert_file × N] → next_wave
  … → finish`. **`Send`**: a conditional edge may return a *list* of
  `Send(node, payload)` instead of a node name, so the fan-out width is data
  (the wave), not wiring; the payload **is** the branch's whole input state (it
  is not merged into the parent), hence
  `add_node("convert_file", convert_file, input_schema=FileJob)`. Sync mode runs
  the branches on a real `ThreadPoolExecutor`.
- **Reducer**: N branches writing one key is `InvalidUpdateError: Can receive
  only one value per step`; `outcomes: Annotated[list[FileOutcome],
  operator.add]` is the join. Two consequences: results arrive in **completion
  order** (`ordered()` re-sorts by `(wave, path)` at read time), and a reduced
  channel can only be appended to, never rewritten — so the sort can never live
  in a node.
- **Subgraph**: `convert_file` invokes `graph.build_graph()` with
  `run_name=f"convert:{path}"`, so LangSmith nests suite → file → attempt → LLM.
  `SuiteSettings` context (model / critic_model / max_attempts / user_id) is
  copied into each child's `RunSettings`.
- `plan` copies `copy`-action files into `out_root` **first** (they are context);
  `dispatch` builds each job's `context_paths` from `out_root` **at dispatch
  time**, which is how wave 2 sees the *converted* companion. A needs-review file
  is still written (the next wave imports it); a branch that raises becomes a
  `failed` row, never a dead suite; `ask_risks=False` always (twelve parallel
  branches have nobody to interrupt).
- `--only PATTERN` (fnmatch on the relative path or the bare name, repeatable),
  `--parallel N` → `config["max_concurrency"]`, `recursion_limit = 2*waves + 6`.
- **Gotcha:** `from __future__ import annotations` makes
  `SuiteState.__annotations__["outcomes"]` a *string*, so pinning the reducer in
  a test needs `get_type_hints(..., include_extras=True)`.

## What 9.1 built (`suite.py` — the scan, no LLM)

- `SuiteFile` / `Manifest`; `discover` (os.walk with `SKIP_DIRS` **pruned from
  the walk**, not filtered after); `import_specifiers` (one regex covering
  `from "x"`, `import "x"`, `export … from`, `require()`, dynamic `import()`);
  `resolve_import` (TypeScript's own order `.ts .tsx .js .mjs .cjs /index.ts
  /index.js`; a non-`.` specifier → `external_imports`; a relative path leaving
  the folder → `None`, a real limitation the report names).
- **The two readings of `classify()`**: a helper with no automation library gets
  `supported=False` from the classifier (right answer to "convert this file")
  but action **copy** from the scanner — "nothing to convert" in a folder is not
  a refusal. Four kinds → three actions; copied and skipped files are in no wave.
- **Waves are Kahn's algorithm layer by layer**, over convertible files only; an
  import cycle gets one final wave plus a note rather than a hang or a silent
  drop. `imports` does double duty: the wait-for edge *and* the file's
  `context_paths`. `imported_by` is for blast-radius reporting.
- `s2p scan <folder>`: table on stderr, `s2p.suite-manifest/v1` on stdout under
  `--json`. The toy 6-file mixed suite is built in a `TemporaryDirectory` inside
  `tests/test_suite.py`, deliberately **not** in `samples/` (whose
  `tsconfig.json` includes `**/*.ts`, so a broken fixture there would fail
  `tsc`). Note it is a **three-deep** chain (BasePage → LoginPage → spec) = 3
  waves.
- **Gotcha that cost two test failures: rich/Typer output is terminal-width
  dependent.** In a `Table` the cells *interleave* when folded, so no string
  trick can reassemble a path — pin the width instead
  (`cli.console.width = 100` in `setUp`, restore `cli.console._width` in
  cleanup; `patch.object(console, "width", …)` fails, the property has no
  deleter).

## What 8.2 built

- `graph.RunSettings` (frozen dataclass: `model`, `critic_model`,
  `max_attempts`) declared as `StateGraph(..., context_schema=RunSettings)`,
  passed per call as `compiled.invoke(inputs, config=…, context=run)`.
- `graph.settings(runtime)` — the required guard: LangGraph does **not** apply
  a context schema's defaults, so `runtime.context` is `None` on any invoke
  without `context=` (every earlier phase, the eval runner, most tests).
- `intake` is the only node that reads the context. It resolves both models and
  the cap once and records them in the state (`state["models"]`); `convert` and
  `critic` read that record via `graph.model_for`, so the label can never
  disagree with the call. A context cap beats one restored from a thread.
- `env.MODEL_ALIASES` (sonnet/opus/fable/haiku) + `env.resolve_model`,
  `env.resolve_roles` (flag > `.env` > default; `--model` moves the critic too
  unless a split was chosen deliberately) and `env.key_missing` (fail fast,
  masked, never prints a value).
- `llm.resolve_name` extracted so `make_model` and `prepare_messages` cannot
  disagree about which model a call is for.
- `cli.py`: `--model`, `--critic-model`, `--json`; `run_config()` puts the same
  choices on the trace as tags + metadata; `json_report()` + `emit()` (one
  stdout writer for every exit path, refusals included).
- `tests/test_config.py` (22, incl. cross-provider). **225 offline tests pass.**
- Live check: `--model haiku --critic-model opus` on `LoginPage.ts` — 2/3
  attempts, 4/4 gates + critic PASS, exit 1 on two `baseURL` TODOs; LangSmith
  showed two Haiku actor spans and two Opus critic spans with `.env` untouched.

## Cross-provider work (same day, after 8.2)

- The preflight runs on **every** convert, not only when `--model` is given: a
  machine whose only key is OpenAI now gets exit 2 and `cli.alternatives()`
  ("keys are set here for openai — run with --model openai:<model>") instead of
  exit 1 after a failed authentication. Every test harness now sets a fake
  `ANTHROPIC_API_KEY`, so the offline suite no longer depends on a real `.env`.
- `llm.check_model(name)` — the preflight: `env.key_missing` first (friendly,
  no imports), then **build the client** (local, no network) so the provider
  itself reports a missing integration package (message rewritten to
  `uv add langchain-x`) or an unsupported provider (LangChain's 28-name list is
  replaced with the shape of a model string).
- `env.PROVIDER_KEYS` now covers 14 providers, `KEYLESS_PROVIDERS` covers local
  and credential-chain ones, and an unlisted provider is **unverifiable, not
  invalid**: `required()` no longer raises, `unverifiable()` reports it, and
  `env.check()` prints it as a `•` line.
- `llm.structured_kwargs(name, for_critic)` — `method="json_schema"` only for
  providers where it is known to work (anthropic, openai); everywhere else the
  default tool-calling path. It used to be hard-coded in the critic node.
- Vendor-specific behaviour is now exactly three things, all in `llm.py`: the
  Anthropic cache marker, the critic `effort` knob, and that JSON-schema list.
- Live: `openai:gpt-5.4` alone on `LoginPage.ts` (4/4 + critic PASS, exit 0) and
  `openai:gpt-5.4` actor + `anthropic:claude-sonnet-5` critic on
  `login.spec.ts` (4/4 + critic PASS, exit 0) — two vendors in one graph, the
  Anthropic critic still getting its 3,301-token cache read. Third run with no
  Anthropic key in the process at all (`S2P_MODEL=openai:gpt-5.4`): 4/4 + critic
  PASS, exit 1 on four honest locator TODOs.
- Not checked before a run: the model *name* (needs a network call). Optional
  providers stay out of `pyproject.toml`; the error names the `uv add`.

## 8.2 sharp edges

- **A context schema's defaults are not applied.** `runtime.context is None`
  without `context=`; always go through `graph.settings()`.
- **`runtime` has a `= None` default** so nodes remain directly callable in
  tests; LangGraph still injects by parameter name.
- **The lap budget has two channels**: context (CLI) and the `max_attempts`
  state input (eval harness, since 6.3). Context wins when set.
- **`--json` must own stdout alone** — never the document *and* the code.
- **Provider keys are only checked when a model is named on the command line**;
  probing on every run would break every offline test.

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

Teach theory before code in **plain, simple English** — assume no LangChain
knowledge and explain every construct as it appears. The 150-line /
one-file-at-a-time rule was removed on 2026-09-06: complete the whole step when
asked, then give one walkthrough with check-yourself questions, and let Varun
review before the next step. Review questions target **agentic workflows /
LangChain / LangGraph / LangSmith** — never SDET or TypeScript fundamentals,
which are his home turf. **End every turn that hands control back with an
explicit "waiting on you" line**; he has twice had to ask "stuck?" after a
finished step. No agents unless asked. Frequent progress updates.

Existing commit/push authorization persists; check `gh auth status` is on
`varunbhatt2193` before pushing (the active account flips between his projects
on this Mac). Repo `/Users/varunbhatt/Downloads/Selenium2Playwright`, main,
remote `https://github.com/varunbhatt2193/selenium2playwright.git`. `.env`,
`out/`, `roadmap.md`, `plan-review.md` stay ignored; never read `.env` or print
a key value; never re-add `roadmap.md` / `plan-review.md` to the repo (Varun's
call — not recruiter-friendly; the public face is README.md + plan.md).

**`src/selenium2playwright/bbb.py` is Varun's Streamlit scratch file and is
staged-but-uncommitted (`AM`) in the index. Leave it alone, and commit with the
pathspec form `git commit -- <paths>`, NEVER `git add … && git commit`** — it is
already in the index, so a bare commit sweeps it in whatever you added (that
happened on 2026-09-07 and had to be undone with `git reset --soft HEAD~1`).
`S2P_MODEL` = actor, `S2P_CRITIC_MODEL` = critic (optional),
`S2P_EMBEDDINGS` = recall (default `openai:text-embedding-3-small`, `off`
supported); eval CLIs default to Opus. Use the existing `.venv` and Node toolchains. Chrome
computer-use works for LangSmith screenshots (crop the sidebar with `sips`,
offset 208 px); close tabs when done.
