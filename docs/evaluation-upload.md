# Phase 6.1 — upload theory, implementation, and report contract

An evaluation dataset is a versioned test fixture for the converter. Uploading it
does not execute the converter. A scored experiment later combines a specific
dataset version, a target function, evaluators, and a recorded configuration.
The 6.1 uploader prepares and verifies the first of those pieces.

## Why the upload has a preflight and a readback

Imagine an uploader scans a directory and finds four files because the other
eight have not been written. A successful HTTP request would create a smaller
benchmark and silently change the denominator. We therefore validate the whole
declared collection before creating a LangSmith client. Missing references,
pending review, wrong companions, and stale browser evidence are errors.

Now imagine the upload stores seven rows and the connection drops. Retrying by
creating fresh UUIDs could duplicate those seven. Our IDs are deterministic:
each is `uuid5(dataset_id, case_id)`. The next run first reads the dataset and adds
only absent rows. It checks all existing rows for conflicts before making any write.

Finally, an HTTP success does not prove the saved dataset contains exactly what
we intended. We read the version timestamp returned by LangSmith and fetch the
examples at that version. Verification compares inputs, reference outputs, and
metadata, including the expected `base` split. It rejects unexpected IDs, missing
rows, changed fields, and duplicate returned identities. A successful receipt is
written only after that comparison passes.

## Three identities answer three different questions

| Identity | Includes | Purpose |
|---|---|---|
| `case_id` | Curated name, e.g. `iframe-test` | Match the same logical case across revisions |
| Row `content_sha256` | Source, companion contents, golden, criteria | Identify the exact conversion task |
| `collection_sha256` | All sorted rows including review and fixture evidence | Identify this entire curated release |

The collection name is `selenium2playwright-v1-<first 12 hash characters>`.
Changing task contents, review notes, or attached validation evidence creates a
different collection identity. The full hash is also checked against dataset
metadata, so the name's shortened suffix is not the only collision check.

LangSmith's dataset version is a separate server timestamp. Later experiments
must record the dataset ID and this exact timestamp; a name or the moving
`latest` tag alone does not pin what an experiment used. The tool records the
Git revision and whether the checkout was dirty too. If dirty, the revision alone
does not reproduce the files; the exported collection and its hashes preserve
the dataset inputs, outputs, and evidence. No model or prompt configuration is
invented for 6.1, because no converter invocation occurs.

## Local preflight: `eval_collection.py`

[`sha256_text()`](../src/selenium2playwright/eval_collection.py) returns a digest
for the exact UTF-8 text supplied to it. It is reused to compare source/reference
snapshots with the files previously checked in the browser.

`build_collection()` first checks the 12 planned IDs, uniqueness of paths, evidence
schema, and all four passing static gates. It then processes cases in stable
`case_id` order. Each case must have recorded review and the correct scenario and
file kind. A test gets exactly one declared POM companion from its own scenario;
a POM gets none. The existing `snapshot_example()` performs safe file reads and
separates input text from reference answers.

For each snapshot, preflight compares source and golden digests with the tracked
fixture report. It requires the expected number of passing source and reference
browser tests with matching identities. Each row receives `fixture_validation`
metadata: timestamp, tool versions, browser settings, file hashes, static gate
outcomes, and the related scenario's individual browser test results.

POM rows link to the tests that exercise them. Those repeated metadata entries
must not be summed as additional browser runs: there are eight browser tests per
framework, not sixteen. Static gate results are explicitly scoped to validating
the full golden suite, not 12 independent generated conversions.

Preflight trusts the curated evidence's provenance. Hash matching detects later
fixture edits; it does not authenticate the runner or automatically rerun tests.
Review and real execution remain necessary when refreshing that report.

## Cloud synchronization: `eval_upload.py`

[`expected_examples()`](../src/selenium2playwright/eval_upload.py) maps deterministic
UUIDs to local rows. UUIDs are namespaced by the remote dataset ID, so the same
case in a different collection gets a different identity.

`missing_examples()` streams every remote example, checks its identity, compares
its complete contents, and returns the absent local rows. This order matters: it
must discover a conflict anywhere in the existing dataset before filling a gap.
Comparing only a stored `content_sha256` would miss a manually edited output
whose metadata hash was not updated.

LangSmith puts split membership into `metadata.dataset_split`. The first live
readback exposed its default `['base']` field and correctly failed our initially
too-literal metadata comparison. The implementation now explicitly uploads that
split and requires exactly that server representation. It does not discard other
unexpected metadata. Regression tests cover a changed split and an added field.

`upload_collection()` reads the content-based dataset name or creates it if
absent. It catches only an actual not-found error for creation; authentication,
network, and server failures propagate. A concurrent creation conflict triggers
a reread. It verifies the collection hash before examining examples, uploads
missing rows, then performs full comparison at an exact version timestamp.

Existing mismatched rows are never automatically overwritten or removed.
A partial write or concurrent conflict may require rerunning the command; it
must not be called successful merely because some rows exist. The script is
intended for a single curator invoking it at a time. Deterministic identities
reduce duplicate risk; they do not turn multiple API calls into a transaction.

## Command entry point: `scripts/upload_eval_dataset.py`

[`write_json()`](../scripts/upload_eval_dataset.py) creates the output directory
and saves readable JSON. `main()` parses arguments, preflights all rows, saves
the complete local snapshot, and exits in preview mode by default.

With `--upload`, it loads the project's `.env` through the existing environment
module, requires only the LangSmith credential, constructs the SDK client, and
closes it in `finally`. The converter's model/provider validation is intentionally
not called. Successful upload produces a receipt with dataset identity, version,
URL, row IDs, counts, creation/resume status, SDK version, elapsed time, and Git
provenance. API credentials are neither printed nor copied into the dataset.

## Run and inspect it

From the repository root:

```sh
# Local preview: no network or model calls.
.venv/bin/python scripts/upload_eval_dataset.py

# Upload/resume, then compare the complete saved dataset at its recorded version.
.venv/bin/python scripts/upload_eval_dataset.py --upload
```

Artifacts default to ignored `out/6.1/dataset/`. `collection.json` contains all
captured row text and metadata; `receipt.json` records a successful verified
upload. `--output-dir` can preserve separate invocations. A failed invocation
does not generate a new successful receipt; if an older receipt is present,
inspect its timestamp instead of treating it as evidence for the failed run.

Uploading requires a usable `LANGSMITH_API_KEY` and external network access.
The existing SDK honors configured LangSmith endpoint/workspace settings.
The script does not share the dataset publicly, change workspace settings,
delete datasets, or run evaluation experiments. The direct `langsmith>=0.12.1`
dependency is declared in `pyproject.toml`; `uv.lock` retains SDK 0.12.1 without
upgrading the existing dependency set.

In LangSmith, open the dataset's **Examples** tab. The initial collection should
show 12 rows. Inspect `case_id`, `scenario`, `kind`, and `review_note`; expand an
example to see full inputs, reference outputs, and `fixture_validation` metadata.
The initial dataset has the `base` split. There are no converter scores yet.

## Reporting boundaries for 6.1 and the next phase

The completion report should distinguish these measured facts:

| Fact | Evidence |
|---|---|
| Coverage | Six scenarios, six POM rows, six test-file rows, eight browser tests |
| Source/golden behavior | Eight passing tests in each maintained framework suite |
| Reference static validity | Four suite-wide gates passed, with no findings |
| Local safety checks | Offline regressions for completeness, stale evidence, retries and conflicts |
| Cloud contents | Exact versioned readback of 12 rows, with expected split metadata |
| Repeatability | Rerun creates zero rows and preserves all example IDs |
| UI availability | User-supplied screenshot shows the correct dataset and 12 examples |

This is a dataset release report, not an estimate of the converter's success rate.
6.1 makes zero model calls by design. Token usage, model cost, critic verdicts,
attempt counts, and generated-code quality are not measured experiment results.
Do not populate them with fabricated scores or label unavailable cost as zero.

For 6.2, add a target adapter that accepts only `inputs`, materializes source and
provided companions into an isolated workspace, calls the graph, and captures
its outputs. References must reach evaluators separately. The graph currently
reads paths on disk, so passing the snapshot dictionary directly is insufficient.
Reuse existing validators in evaluator functions, preserving missing outputs and
unavailable tools as explicit outcomes. The main success denominator must include
every scheduled example. Keep test-file and POM summaries distinguishable.

Capture dataset ID/version, code revision, prompt hashes, tool/model settings,
attempts, gate findings, final status, critic feedback, wall time, actor/critic
token usage, and actually available cost data. The detailed experiment contract
remains in [evaluation-dataset.md](evaluation-dataset.md). Browser checks on curated
goldens do not automatically validate future model-generated outputs; a runtime
evaluator remains a separate implementation decision.

## Verification tests and limits

`tests/test_eval_dataset.py` exercises 11 offline test methods. It checks complete
coverage and answer separation, stable ordering, missing/duplicate/pending cases,
stale or missing fixtures, self-answer/path rejection, two uploads without duplicate
writes, recovery from a partial upload, refusal to overwrite edited remote code,
foreign datasets/unexpected rows, corrupt versioned readback, and strict split
metadata handling. The fake service models interrupted writes and server-added
split metadata. Live SDK upload/readback then checks the real integration.

The collection is immutable by convention, not by server access control. Someone
with access can still edit it in LangSmith. The uploader detects drift on rerun,
and future experiments should use the recorded historical version. External
application/CDN changes, new browser builds, and later tool updates require new
fixture checks; the initial report is evidence for its recorded execution only.

Official references: [programmatic dataset management](https://docs.langchain.com/langsmith/manage-datasets-programmatically),
[dataset versions and splits](https://docs.langchain.com/langsmith/manage-datasets),
[dataset UI](https://docs.langchain.com/langsmith/manage-datasets-in-application).
The implementation was also checked against the installed SDK's signatures and
its `DatasetVersion.as_of` and `Dataset.url` fields.
