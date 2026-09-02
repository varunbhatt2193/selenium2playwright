# Roadmap — small code, every line reviewed

This is the execution sequence for the project in `plan.md`. Same architecture, same M0–M8 milestones — decomposed into ~40 steps small enough that every line of code gets reviewed and understood by **you** before the next piece exists. `plan.md` answers *what and why*; this file answers *in what order, tonight*.

---

## The working agreement

1. **One step per sitting.** Don't start a step until the previous one's ✅ *Done when* check passes. Tick the box in this file.
2. **Each step runs the same way:** Claude explains the concept (with the official doc to read) → we sketch the interface together (state shape, function signature) → **Claude writes the code, <100 lines at a time** → walkthrough of what each part does and why → **you review, ask questions, request modifications** → run the *Done when* check.
3. **Nothing advances un-reviewed.** The next increment doesn't start until you can explain every line of the current one. "Looks fine" isn't review — ask *why* until it's true.
4. **Modification requests are first-class.** Naming, style, a different approach — Claude implements them (or pushes back briefly with a reason, and you decide).
5. **Model policy:** develop and learn on `claude-sonnet-5` (cheap); switch to `claude-opus-5` for eval runs and demos. Made runtime-configurable in Phase 8.
6. **No prompt/playbook change without a green eval run** — enforceable from Phase 6 onward.
7. **Session hygiene:** when Claude's context grows heavy, it will say so — `/compact` mid-step, `/clear` + fresh session at step boundaries. Safe by design: this file's checkboxes + Claude's memory carry all state across sessions.

## Deliberate changes vs plan.md (for smallness)

- **`the-internet.herokuapp.com` replaces the custom demo app.** It already has login, JS alerts/confirms, iframes, multiple windows, file upload, and dynamic-loading spinners — every hard case on the list — plus an official Docker image (`gprestes/the-internet`) for CI later. Cuts ~2 sessions of Express work; every SDET reviewer recognizes it instantly. (Reversible: build a custom app at Phase 11 if you want the branding.)
- **Starter evals pulled up to right after the reflection loop** (plan had them at M5) — per plan-review amendment #6.
- **HITL `interrupt()` lands in the memory phase; suite fan-out is its own phase** — per amendment #7.
- Everything else follows `plan.md` + the amendments in `plan-review.md` §4.

---

## Phase 0 — Toolchain + first traced call *(≈ M0 start · LCAE: chat models, tracing)*

- [x] **0.1 Project skeleton + public repo.** *(done 2026-09-02 — commit 8a7964e)* Build: `uv init`, `pyproject.toml`, `.gitignore` (`.env`, `__pycache__`, `node_modules`), `git init`; create the **public** repo at `github.com/varunbhatt2193/selenium2playwright` and push. **WIP signaling** (recruiters watch this account): keep the repo *name* stable — launch-day links must never break — and carry the 🚧 in the repo description + README H1 ("🚧 Selenium2Playwright — work in progress, building in public"). README v0 = one-paragraph pitch + status line ("Phase 0 · M0 in progress") + a phase checklist mirroring this roadmap. Commit messages recruiter-readable from day 1.
  *Done when:* `uv run python -c "print('s2p')"` works and the repo is live with the WIP README pushed.
- [ ] **0.2 Secrets.** Build: get Anthropic + LangSmith API keys, `.env`, load with `python-dotenv`, tiny script asserting the vars exist (print masked).
  *Done when:* script passes and `git status` shows `.env` untracked.
- [ ] **0.3 First model call.** Build: `langchain-anthropic`; `init_chat_model` → invoke one message: "convert this one Selenium line to Playwright" (~15 lines).
  *Learn:* chat model interface, message roles. *Done when:* a sensible response prints.
- [ ] **0.4 Tracing on.** Build: nothing — set `LANGSMITH_TRACING=true` + key, rerun 0.3.
  *Learn:* reading a trace (tokens, cost, latency). *Done when:* you can open your call's trace URL and explain each field.

## Phase 1 — Sample Selenium code, no AI *(your home turf · sets up validation + evals)*

- [ ] **1.1 Samples workspace.** Build: `samples/` with `package.json`, `tsconfig.json`, install `selenium-webdriver`, `mocha`, `chai`, types.
  *Done when:* `npx tsc --noEmit` passes. (This exact tsconfig becomes the validation sandbox in Phase 4.)
- [ ] **1.2 First sample pair.** Build: `LoginPage` POM + `login.spec.ts` (Mocha+chai) targeting `the-internet.herokuapp.com/login`.
  *Done when:* compiles; bonus if it runs green.
- [ ] **1.3 Golden Playwright version.** Build: the *answer key* — the ideal Playwright version of 1.2 (`@playwright/test`), written as native Playwright from scratch, never by running any AI conversion (the answer key must be independent of the system it will grade). In Phase 6, evaluators score the agent's output against this file: agent output = actual, golden = expected.
  *Done when:* compiles and runs green — and it has survived your toughest SDET review, because if the answer key is wrong, every eval score afterward lies.
- [ ] **1.4 Playbook v0.** Build: `docs/playbook.md` — the mapping rules you just used (explicit waits→auto-wait, `By.*`→locators, chai→`expect`, hooks→fixtures…). Prose only.
  *Done when:* ~20 rules listed. This literally becomes the system prompt.

## Phase 2 — One-shot converter script *(≈ M0 done · LCAE: prompts, structured output)*

- [ ] **2.1 Prompt v1.** Build: system prompt = playbook contents; human = sample code; print raw output.
  *Done when:* you've diffed the output against your golden and written down every gap.
- [ ] **2.2 Structured output.** Build: Pydantic `ConversionResult(code, notes, todos)` + `.with_structured_output()`; write `result.code` to a `.ts` file.
  *Learn:* why schemas beat parsing. *Done when:* generated file lands on disk.
- [ ] **2.3 Feel the gap.** Build: nothing — run `tsc --noEmit` on the generated file by hand; catalog the failures.
  *Done when:* you have a written failure taxonomy. **🏁 M0 shipped: traced one-prompt conversion.**

## Phase 3 — First LangGraph *(≈ M1 · LCAE: StateGraph, state, conditional edges)*

- [ ] **3.1 Two-node graph.** Build: `ConversionState` (TypedDict), nodes `intake` + `convert` (wraps 2.2), edges, `.compile()`, invoke (~40 lines).
  *Done when:* same output as 2.2 but the LangSmith trace now shows named nodes.
- [ ] **3.2 Classify + refuse.** Build: `intake` detects framework from imports (selenium-webdriver? WebdriverIO? Mocha vs Jest) — heuristics, no LLM; conditional edge → `convert` or `refuse` (honest "not supported" with reason).
  *Learn:* conditional edges; honesty as a feature. *Done when:* a WDIO file gets a clean refusal.
- [ ] **3.3 Graph picture.** Build: render the graph's mermaid into README.
  *Done when:* diagram is in the repo.

## Phase 4 — Deterministic validators *(≈ M2 part 1 · the "can't lie to the compiler" layer)*

- [ ] **4.1 Compile gate.** Build: `sandbox/` (pinned tsconfig + ambient `.d.ts` stubs for unresolved imports); `validators/compile.py` runs `tsc --noEmit` via subprocess, parses errors into a Pydantic `ValidationReport`.
  *Done when:* golden passes; a deliberately broken file yields structured errors.
- [ ] **4.2 Residue scan.** Build: forbidden-pattern scan (selenium imports, `driver.` calls) — regex on imports first, AST only if regex proves insufficient.
  *Done when:* golden clean, original sample flagged.
- [ ] **4.3 Lint gate.** Build: flat-config ESLint + `typescript-eslint` (**`no-floating-promises`**) + `eslint-plugin-playwright`; `validators/lint.py` wrapper.
  *Learn:* missed `await` is the #1 conversion bug class. *Done when:* a missing-await file gets caught.
- [ ] **4.4 Validate node.** Build: node running all three validators, merging into `state.validation`; edge → END (report-only for now).
  *Done when:* one invoke prints a per-layer pass/fail scorecard.

## Phase 5 — Reflection loop *(≈ M2 done · the flagship pattern)*

- [ ] **5.1 Critic node.** Build: prompt = generated code + `ValidationReport` → structured `Critique(verdict, fixes[])`.
  *Learn:* actor–critic; the critic reads the compiler, not vibes.
- [ ] **5.2 The loop.** Build: conditional edge critic → `convert` (critique + iteration+1) or → `assemble`; hard cap 3; `assemble` always emits code + scorecard + TODOs (never silent failure).
  *Done when:* you watch a failing conversion fix itself within 3 laps in the trace. **🏁 M2 shipped: self-correcting converter.**

## Phase 6 — Evals v0 *(pulled early per review · LCAE: datasets, evaluate(), judges)*

- [ ] **6.1 Dataset.** Build: grow samples to ~5 POMs + ~8 tests across the-internet pages (login, alerts, iframe, windows, upload, dynamic loading); upload script → LangSmith dataset.
  *Done when:* dataset visible in the UI.
- [ ] **6.2 Deterministic evaluators.** Build: compiles? residue-free? no-floating-promises? as evaluator functions; run `evaluate()`.
  *Done when:* first experiment page with scores.
- [ ] **6.3 A/B the reflection.** Build: nothing new — run the experiment with `max_iterations=0` vs `3`; screenshot the delta.
  *Done when:* you have the number (e.g. "compile-pass X%→Y%"). **This number replaces the word "perfect" everywhere.**
- [ ] **6.4 LLM-as-judge.** Build: `openevals` judge for idiomatic quality (locator choice, web-first assertions) with a rubric; sanity-check it against your goldens.
  *Learn:* judge ≠ ground truth; rubric design.

## Phase 7 — Memory + HITL *(≈ M3 · LCAE: checkpointer, interrupt, Store)*

- [ ] **7.1 Short-term: checkpointer.** Build: `SqliteSaver` + `thread_id`; turn 2 = "now apply my naming convention" refines the same conversion.
  *Done when:* two-turn refinement works. *Learn:* threads are short-term memory.
- [ ] **7.2 HITL: `interrupt()`.** Build: semantic-risk detector (dialogs, `executeScript`, shared login state) triggers `interrupt()` with a question; resume with `Command(resume=…)`.
  *Done when:* a risky sample pauses and your answer changes the output.
- [ ] **7.3 Long-term: Store.** Build: put/get user conventions (naming, fixture style) in the Store, injected into the convert prompt; pick + configure the embeddings provider for semantic search (decision: `voyage-3.5-lite` or `text-embedding-3-small` — Anthropic ships none).
  *Done when:* a convention taught in thread 1 auto-applies in a fresh thread 2.

## Phase 8 — CLI *(≈ M3.5 · gives suite mode a surface)*

- [ ] **8.1 Typer skeleton.** Build: `s2p convert <file>` with a rich scorecard table + before/after diff; `[project.scripts]` entry.
  *Done when:* `uv run s2p convert samples/...` works end-to-end.
- [ ] **8.2 Config flags.** Build: `--model`, `--max-iterations`, `--json` wired through a context/config schema (runtime-configurable model, not env-var swap).
  *Learn:* configurable graphs. *Done when:* `--model sonnet` visibly changes the traced model.

## Phase 9 — Suite mode *(≈ M4 · LCAE: Send API, reducers, subgraphs)*

- [ ] **9.1 Suite scan.** Build: walk a directory, classify files (POM/test/support/unsupported), emit a manifest + wave plan (POMs → tests). No LLM.
  *Done when:* manifest JSON for a toy 6-file suite.
- [ ] **9.2 Fan-out.** Build: per-file convert→validate→critic subgraph dispatched via `Send`; reducer collects results.
  *Done when:* 3 files convert in parallel in one trace.
- [ ] **9.3 Suite assemble.** Build: whole-tree `tsc`, aggregate scorecard, per-file parity ledger (kept/renamed/removed-with-reason), suite report md.
  *Done when:* `s2p suite ./samples/selenium-suite` emits a converted tree + report. **🏁 M4 shipped: the wow demo.**

## Phase 10 — Deploy, playground, monitor *(≈ M5+M7 · full ADLC)*

- [ ] **10.1 Local platform.** Build: `langgraph.json`; run `langgraph dev`; poke the graph in Studio.
- [ ] **10.2 Deploy.** Build: LangSmith Deployment dev tier; call the cloud graph from your laptop via `langgraph-sdk`.
  *Done when:* a cloud URL converts your sample.
- [ ] **10.3 Playground.** Build: Streamlit — paste box + **one-click sample buttons** → diff + scorecard + 👍/👎 feedback.
  *Done when:* local Streamlit runs against the cloud backend.
- [ ] **10.4 Guardrails before the URL is public.** Build: rate limit, spend alert, keep-warm ping, feedback→dataset flywheel tag, online eval on sampled traffic.
  *Done when:* alert fires on a test overspend; a 👎 lands in the dataset queue.

## Phase 11 — Hardening + launch *(≈ M6+M8 · post-cert)*

- [ ] **11.1 Hard-case sprint.** Add the 12 SDET hard cases as dataset rows; iterate playbook/prompts, gated by evals.
- [ ] **11.2 Execution evals in CI.** Docker `the-internet`; run converted goldens headless on the curated set only; GitHub Action gate.
- [ ] **11.3 Launch kit.** README: **drop the 🚧 WIP banner**, add codemod comparison table, cost numbers, ADR links, public trace links; demo video; LinkedIn assets per `plan.md` §12–13.

---

*Rough pacing: Phases 0–2 ≈ one week of evenings; M2 (self-correcting converter) ≈ week 3; suite mode ≈ week 6; deployed ≈ week 8. At your pace — the boxes don't expire.*
