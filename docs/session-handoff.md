# Restart here — 2026-09-05

## Current position

Phase 5.2 is implemented, reviewed, committed, and pushed. The next implementation
step is **6.1: the evaluation dataset**. The user requested theory and code first;
that lesson was delivered and is preserved in [evaluation-primer.md](evaluation-primer.md).
User understanding/review of the lesson has not yet been confirmed. No Phase 6
dataset upload, evaluator module, experiment runner, or cloud experiment has been
created. Do not mistake teaching snippets for implemented features.

The latest request was to save, update, commit, and push the project before the
user clears the session. Resume from these notes and the user's next instruction;
there is no request to start Phase 6 automatically or repeat the entire lesson.

## Working agreement

- Teach in simple language tied to the user's SDET experience. Explain where code
  lives and why it exists before advancing.
- Work one roadmap step at a time. Explain the concept and interface, write less
  than 150 lines per patch, walk through the code, allow review, then verify the
  completion check. Multiple explained patches can make up one step.
- Preserve the user's review point; delivered explanations do not establish that
  the user has understood or approved the next increment.
- Report progress during longer work and follow existing commit/push authorization.
- Once the evaluation baseline exists, prompt/playbook changes need a green eval.

`roadmap.md` contains the detailed local sequence and is intentionally gitignored,
as is `plan-review.md`. They remain on this machine across session resets. This
tracked handoff preserves the restart point for fresh checkouts too. `plan.md`
is the broader architecture; its milestone order predates the early eval phase.

## Completed implementation and evidence

- `ee8fa85`: Step 4.5, graph validation and scorecard.
- `0d5ad94`: Step 5.1, structured critic grounded in source and validation evidence.
- `a57076f`: Step 5.2, bounded repair loop and final conversion report.
- Last full verification: **36 offline tests passed** before the 5.2 commit.
  Subsequent work only saved documentation; no application code has changed.
- The latest lesson's local compiler probe returned 1 for golden LoginPage, 0
  for a `.fill()` to `.fil()` mutation, and 0 for empty code. No live model call
  or cloud evaluation was used for the lesson.

The cap is **three total conversion attempts: initial draft plus two repairs**.
Every new draft is validated and reviewed afresh. Failed deterministic gates
cannot be overridden by a critic pass. Tool/critic failures stop rewrites; failed
repairs retain the previous draft. Final open TODOs mean `needs-review` without
additional rewrites. Token usage includes all attempts.

The live seeded missing-await demo repaired its first draft on attempt 2. All
four gates and the critic passed; two locator TODOs correctly kept `needs-review`
and exit code 1. This demonstrates repair mechanics, not general conversion
quality. Run ID: `64ce108a-d3e7-46e6-87b2-cc660f2f50cf`. Artifacts and the private
trace URL are in ignored `out/5.2/`. Replay script: `scripts/demo_reflection.py`.
The full explanation is in [reflection-loop.md](reflection-loop.md).

## Next: Phase 6

1. **6.1 Dataset:** grow samples to roughly five POMs and eight tests covering
   login, alerts, iframe, windows, upload, and dynamic loading on the-internet.
   Preserve source/companion contents and independent reviewed golden outputs.
   Prepare the LangSmith upload script. Done when the dataset is visible in the UI.
2. **6.2 Deterministic evaluators:** reuse compile, residue, and typed lint checks
   in evaluator functions and run the first scored experiment. Preserve tool
   errors and missing outputs explicitly; handle companion imports.
3. **6.3 Reflection comparison:** one attempt versus up to three total attempts,
   holding other settings and the dataset fixed. Record quality, time, and usage.
   The current graph has no `max_iterations` input; a configurable cap is still
   needed to run this comparison cleanly.
4. **6.4 LLM judge:** explicit idiomatic-quality rubric, calibrated against human
   reviews/goldens. Judge verdicts and compile scores do not prove runtime behavior.
5. **6.5 Model comparison:** repeat the curated experiment per configured model.
   Verify available model IDs when implementing; do not invent benchmark numbers.

## Files and environment

- Runtime: `src/selenium2playwright/graph.py`, `reflection.py`, `schemas.py`,
  `prompts.py`, `llm.py`, and `validators/`.
- Existing examples: `samples/selenium-suite/` and `samples/playwright-golden/`,
  each currently containing LoginPage and its login test file.
- Full offline suite: `.venv/bin/python -m unittest discover -s tests -v`.
  Repeat after relevant code changes, not just to reconstruct this session.
- Live demonstration: `.venv/bin/python scripts/demo_reflection.py`; this makes
  provider calls and writes ignored artifacts. No rerun is needed for the handoff.
- Model selection uses `S2P_MODEL` through `env.py`/`llm.py`. Local development
  currently uses `anthropic:claude-sonnet-5`; provider credentials stay in `.env`.
  The Anthropic workspace header is already handled by `llm.py`.
- Branch: `main`; remote: `https://github.com/varunbhatt2193/selenium2playwright.git`.
  `.env`, virtual environments, node modules, sandbox work, and `out/` remain
  ignored. Do not force-add secrets, generated files, or the private roadmap.

Read this file, the primer, and the local roadmap if present. Then continue the
user's learning/review or Step 6.1 according to their new instruction.
