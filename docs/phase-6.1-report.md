# Phase 6.1 completion report — 2026-09-05

**6.1 is complete.** The curated dataset contains 12 conversion examples across
six scenarios. It has been uploaded to LangSmith, verified through full versioned
API readback, and confirmed visible in the user's screenshot of the Examples tab.
The screenshot shows all 12 examples and the matching dataset name and ID.

Open [the dataset in LangSmith](https://smith.langchain.com/o/32ac11b4-3e72-4765-a59b-5dc1bcd32cbe/datasets/33c80b1e-96bd-4b5b-a9c1-ca49d215828f).
Access uses the user's existing workspace permissions. The upload did not make
the dataset public. The exact receipt is in [phase-6.1-receipt.json](phase-6.1-receipt.json).

## Dataset identity and contents

| Field | Recorded value |
|---|---|
| Name | `selenium2playwright-v1-4920b5f319d8` |
| Dataset ID | `33c80b1e-96bd-4b5b-a9c1-ca49d215828f` |
| Version, UTC | `2026-09-06T03:05:09.476354+00:00` |
| Collection SHA-256 | `4920b5f319d827a25a3d8f1a2f026c430e1fb20bdb897c6b9c3599f55b8aeb3d` |
| Examples | 12: six POMs and six test files |
| Scenarios | Login, alerts, iframe, windows, upload, dynamic loading |
| Browser tests | Eight per framework; a file may contain multiple tests |
| Split | `base` |
| SDK | LangSmith Python 0.12.1 |

Each POM example is standalone. Each test-file example includes its scenario's
golden Playwright POM in `inputs.context_files`. All rows capture source contents,
independent reference code, acceptance criteria, role/scenario identifiers,
review notes, fingerprints, and measured fixture-validation evidence. Reference
code for the target and acceptance criteria remain outside converter inputs.

Login and alerts retain recorded user review. Iframe, windows, upload, and dynamic
loading record agent review under the user's instruction to finish 6.1. Browser
passes support that review but are not relabeled as human approval. The golden
files were authored independently of the conversion graph; no converter outputs
were promoted to references during this step.

## Measured validation

| Check | Result | Scope and limits |
|---|---|---|
| Source browser suite | 8 passed, 0 failed/pending; 19.426 seconds | Maintained Selenium tests, headless Chrome |
| Golden browser suite | 8 passed, 0 skipped/unexpected/flaky; 18.661 seconds | Headless Chromium, one worker, retries 0 |
| Sample typecheck | Passed | Both maintained TypeScript suites |
| Compile | Passed, no findings | All 12 golden files together |
| Residue | Passed, no findings | All 12 golden files together |
| Typed lint | Passed, no findings | All 12 golden files together |
| Parity | Passed, no findings | Eight matching tests; assertions 11 source → 12 golden |
| Offline regressions | 47 passed in 34.853 seconds | Includes 11 dataset/upload test methods |
| Versioned server readback | 12 examples verified | Full inputs, outputs, metadata and expected split |
| Repeat upload | 0 rows created; same 12 verified | Deterministic IDs and conflict checks prevent duplicates |
| LangSmith UI | 12 examples visible | User supplied screenshot; agent browser connection unavailable |

Per-case test names, durations, tool versions, hashes, and gate scopes are recorded
in [evaluation-fixture-evidence.json](evaluation-fixture-evidence.json) and attached
to each example's `fixture_validation` metadata. POMs refer to the tests exercising
them; repeated metadata must not be counted as extra browser runs. Full source/
golden method explanations are in [evaluation-fixtures.md](evaluation-fixtures.md).

The first live write stored the 12 examples but verification rejected an extra
server field: LangSmith's default `metadata.dataset_split = ['base']`. The uploader
now explicitly selects and checks that exact split. A regression test rejects a
different split or unexpected metadata. A subsequent invocation verified the
existing dataset and created zero rows. No examples were deleted or overwritten
to resolve the mismatch.

The first full offline run also exposed a stale source-line expectation in an
existing parity test after login gained headless setup. That test now locates the
expected assertion line in the source, preserving its location-checking intent.
The final 47-test run passed after these corrections.

## Reproduction and artifact provenance

The [upload walkthrough](evaluation-upload.md) explains every helper and the
preflight → snapshot → upload/resume → versioned-readback sequence. Preview with
`.venv/bin/python scripts/upload_eval_dataset.py`; upload/verify with the same
command plus `--upload`. The saved collection digest should match this report
as long as fixture and curation metadata remain unchanged.

The initial successful readback ran from a dirty checkout based on `86707d0`.
Its ignored receipt accurately records that state. The tracked receipt is from
a subsequent verification of the committed implementation and records its Git
revision. Documentation/receipt bookkeeping can have a later commit without
changing the dataset contents or version. Full local snapshots and receipts
remain under ignored `out/6.1/`; browser reports/traces are under
`samples/out/6.1/completion/`. The user's UI screenshot is retained locally at
`out/6.1/completion/langsmith-examples.png`, rather than included in the repository.

This report uses the local session date. Recorded browser/upload timestamps are
UTC on 2026-09-06; retain the timezone when comparing them with UI timestamps.

## What the completed step does and does not establish

The dataset is ready for converter evaluation. This report measures curated
fixtures and dataset integrity, not conversion success, reflection improvements,
or model rankings. No actor, critic, judge, or scored experiment ran in 6.1.
The script makes zero model calls; model-token/cost and converter-quality metrics
are not measured. The UI has no scored experiment from this step.

Iframe typing, nested frames, JS prompt input, confirmation acceptance, broad
suite dependencies, and upload-byte verification remain outside this initial
matrix. Browser checks used a live public application and external TinyMCE CDN;
one run per case is not a reliability estimate. Exact browser binary versions
were not recorded. Static passes do not prove runtime behavior, as the earlier
alerts mutation demonstrates. No runtime evaluator was integrated into the graph.

Next is **6.2**: materialize snapshot inputs for the graph, wrap deterministic
validators as evaluators, and run the first scored experiment with a detailed
per-example report. Preserve explicit missing-output/tool-error outcomes and
record dataset version, code/prompt/tool/model configuration, attempts, findings,
status, time, usage, and available cost. Do not silently advance to 6.3 or tune
prompts/playbook before the evaluation baseline is established.
