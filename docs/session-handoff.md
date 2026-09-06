# Restart here — 2026-09-05

## Current position

Phase 5.2 is implemented, reviewed, committed, and pushed. The user has now said
they are ready for Phase 6 and requires detailed theory before code, explanatory
comments in the repository, and detailed LangSmith reports.

**6.1 has started; the user approved committing the first increment and continuing.**
`src/selenium2playwright/eval_dataset.py` contains a 109-line local snapshot
builder: `DatasetCase` and `snapshot_example`. Read the detailed theory, code
walkthrough, reporting design, and local probe evidence in
[evaluation-dataset.md](evaluation-dataset.md). The earlier introductory lesson
remains in [evaluation-primer.md](evaluation-primer.md).

The builder captures source/companion/reference text and acceptance criteria,
records review metadata, and fingerprints captured inputs/outputs. It rejects
invalid paths and the target's own reference as a companion. It is not yet wired
to the graph: a later target adapter must materialize snapshots for file intake.
No expanded samples, upload script, evaluator module, runner, or cloud experiment
has been created. Continue with the coverage matrix and manifest,
then source/golden pairs and upload. Do not advance into 6.2 before 6.1 is complete.

## Working agreement

- Teach in simple language tied to the user's SDET experience. Explain where code
  lives and why it exists before advancing.
- Work one roadmap step at a time. Explain the concept and interface, write less
  than 150 lines per patch, walk through the code, allow review, then verify the
  completion check. Multiple explained patches can make up one step.
- Preserve the user's review point; delivered explanations do not establish that
  the user has understood or approved the next increment.
- Phase 6 specifically requires detailed theory before each code increment,
  explanatory code comments, and detailed, evidence-backed LangSmith reports.
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
- Last full verification: **36 offline tests passed** after the first 6.1 snapshot
  increment. Additional local probes checked snapshot contents, SDK format,
  fingerprints, path rejection, and self-reference exclusion. These probes are
  not persisted tests or a live evaluation; details are in evaluation-dataset.md.
- The introductory lesson's local compiler probe returned 1 for golden LoginPage, 0
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
   Snapshot builder is implemented and approved; curation/upload remain.
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
