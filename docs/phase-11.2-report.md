# Step 11.2 — execution evals: the fifth measurement, and what it caught

The four gates read the code. This step runs it.

`compile`, `residue`, `lint` and `parity` can all pass while a converted test
quietly checks nothing — that is the premise the hard cases were built on. So the
converted files now go into a real browser, against a pinned local copy of the
demo app, and the browser gets a vote.

**No model was called anywhere in this step.** Finished experiments already
contain the code they produced, so every number below was obtained by replaying
saved conversions through Chromium. Total spend: zero tokens, about four minutes
of browser time.

---

## Failures first

| What failed | Where | Why it matters |
| --- | --- | --- |
| `dynamic-loading-test` (Phase 6.2, Opus) | all four gates green | The source waited **10 000 ms** for a ~5 s delayed element. The conversion dropped the budget, so Playwright's **5 000 ms** default loses the race. Reproduced 3 runs out of 3 — not a flake. |
| `dynamic-loading.spec.ts` (Phase 9.3 suite run) | whole converted tree | **The same defect, in an independent run, months apart.** Two independent samples make this a defect class, not variance. |
| `upload-page`, `nested-frames-page`, `shared-session-page` | all four gates green | The converted page object is good code that names its members differently from the caller it was never shown. See "the coin flip" below. |
| `dynamic-controls-page` (arm C) | compile also red | An invented matcher, `expect(...).toBeDetached()`. Already covered by playbook rule 23; recorded, not given a new rule. |
| `windows-page` (Phase 6.2) | no code at all | The known structured-output parse failure. It cannot be executed, so it is counted as `not_run` and never as a pass. |

The dropped timeout budget is a **candidate playbook rule** — *an explicit wait
longer than Playwright's default is information: delete the wait, keep the
budget*. It is logged in `docs/gap-log.md` (T12) and **not** added to the
playbook, because the working agreement is no prompt change without a green eval
run, and this project does not edit the playbook to fit a fixture it just read.

---

## The number

Execution pass, by saved experiment. The denominator is every scheduled row.

| Experiment | Benchmark | Execution | Test rows | Page-object rows |
| --- | --- | --- | --- | --- |
| Phase 6.2 baseline (`claude-opus-5`) | base, 12 rows | **9 / 12** (75.0%) | 5 / 6 | 4 / 6 |
| 11.1b probe (`gpt-5.4`) | hard, 11 rows | **10 / 11** (90.9%) | 5 / 5 | 5 / 6 |
| 11.1b arm A (`gpt-5.4`) | hard, 11 rows | **9 / 11** (81.8%) | 5 / 5 | 4 / 6 |
| 11.1b arm B (+ rules 26/27) | hard, 11 rows | **9 / 11** (81.8%) | 5 / 5 | 4 / 6 |
| 11.1b arm C (+ rule 26 completed) | hard, 11 rows | **8 / 11** (72.7%) | 5 / 5 | 3 / 6 |

**The playbook edits did not move the execution number.** They moved the graph
number (6/11 → 9/11, step 11.1b) and they left this one flat, inside the same
run-to-run variance 11.1b already documented. Read together, the two
measurements say the same thing: with 11 rows and one run per arm, a delta of
one or two rows is not evidence.

## The coin flip

Split the rows by kind and the picture stops being noisy:

* **Test rows: 20 / 20 on the hard benchmark, across four runs.** A test row is
  handed golden page objects, so its interface is *supplied* and execution
  measures behaviour. The one test-row failure anywhere is `dynamic-loading-test`
  above — a real bug, found exactly as intended.
* **Page-object rows: 20 / 30.** A page object is executed against the golden
  caller, which names members the converter was never shown.

Here is the whole difference between a pass and a fail on `shared-session-page`,
from two runs with **byte-identical configuration**:

```ts
// 11.1b probe                       // 11.1b arm A
readonly flash: Locator;             readonly flashMessage: Locator;
```

The golden test says `securePage.flash`. Both conversions are good, idiomatic
Playwright; one of them guessed the caller's noun and one did not. Nothing in
the input tells the model which. That is the same wall hard case 10 hit in
11.1b — *"call sites were not provided"* — arriving from the other direction.

**So: a page-object row cannot be scored end-to-end by a single-file
conversion.** The unit that can be executed is a suite, where the page objects
and their callers are converted together. Which is why the next table exists.

## Whole-tree execution

The Phase 9.3 suite run converted all 12 files in one pass, so its interfaces are
its own and nobody had to guess:

| Tree | Result |
| --- | --- |
| `samples/playwright-golden` (the fixtures) | 7 passed, 1 declared divergence, **gate green** |
| `samples/playwright-hard-golden` | 7 passed, **gate green** |
| `out/9.3` (12 files, converted as one suite) | 6 passed, 1 declared divergence, **1 real failure** |

The single failure is the dropped timeout budget. No interface mismatch appeared
anywhere in the tree — the argument above, measured rather than asserted.

---

## The app is pinned, and it is not production

`deploy/the-internet/compose.yml` runs `gprestes/the-internet` **by digest**, not
by tag: `latest` was last pushed in 2020 and could move tomorrow. The digest is
in the compose file, in `execution.APP_IMAGE`, and in the CI workflow, and a test
asserts the three agree.

That pinned build is **the-internet 0.58.0, and production has moved since**. One
golden assertion depends on a spelling upstream fixed after 2020:

| | text |
| --- | --- |
| pinned app | `You successfuly clicked an alert` |
| production | `You successfully clicked an alert` |

The golden asserts production's text and **cannot be edited** — its SHA-256 is
part of the published dataset `selenium2playwright-v1-4920b5f319d8`, so changing
it would silently retire the benchmark every earlier number was measured on.

So the divergence is *declared*, in `execution.KNOWN_APP_DIVERGENCES`, measured
by running the same untouched golden against both apps rather than assumed. A
declared divergence is excluded from pass/fail and named in every report — and
**if one ever starts passing, the gate fails**, because that means the app moved
and the list is stale. There is exactly one entry.

## The gate

`.github/workflows/ci.yml`, on every push and pull request, no secrets, no model
calls, no LangSmith — a pull request cannot spend money here.

* **offline-checks** — the 485-test suite, which drives the real gates (tsc,
  ESLint, both AST scripts) against scripted model replies. It now runs with no
  credentials at all; three tests that needed a key-shaped string got one. It also
  runs on `uv sync --frozen`, without the optional `ui` group — the 57 playground
  tests still pass with Streamlit uninstalled, which is the 10.3 split doing its
  job rather than a coincidence.
* **execution-gate** — the golden fixtures of both benchmarks in a real browser
  against the service container. Exit 1 means a fixture ran red or a divergence
  went stale; exit 2 means the app never answered, which is infrastructure
  failure and must never be reported as a passing suite.

The second job is the one this step exists for. Every converter score in this
repository is measured against those fixtures. If a dependency bump, a sandbox
change or an edited fixture quietly breaks them, every published percentage
starts meaning something else — and now that shows up on the commit that did it.

## One side effect worth knowing

`eval_plan.configuration()` hashes **all** of `src/**/*.py`, so adding these two
modules changes the configuration fingerprint even though neither can affect a
conversion. That is the hash being deliberately coarse rather than clever, and
the consequence is the same one arm A hit in 11.1b: **both arms of any future
A/B must run at or after this commit**, never one before and one after.

## What this does not measure

* **Not production correctness.** It is a 2020 build of one demo app.
* **Not user code, ever.** Executing generated code is evaluation-only
  infrastructure, on our own curated fixtures. The deployed converter's
  validation stays static — `tests/test_execution.py` asserts that no module the
  graph, CLI or service can reach imports this harness.
* **Not flakiness.** One worker, no retries, one repetition. A row that passes
  here passed once.
* **Not the base benchmark's page-object rows fairly** — see the coin flip.

## Reproduce it

```bash
docker compose -f deploy/the-internet/compose.yml up -d
uv run python scripts/run_execution_eval.py --goldens --suite base     # the gate
uv run python scripts/run_execution_eval.py --goldens --suite hard
uv run python scripts/run_execution_eval.py --experiment out/11.1b/arm-c-playbook2
uv run python scripts/run_execution_eval.py --tree out/9.3 --suite base
docker compose -f deploy/the-internet/compose.yml down
```

Receipts land in `out/11.2/` as JSON plus markdown, each one naming the image
digest, the git revision, and the SHA-256 of every file it ran. `out/` is
gitignored, so the consolidated evidence is committed as
[`docs/phase-11.2-execution.json`](phase-11.2-execution.json) — every aggregate,
every per-row status, and the hash of every file that was executed.
