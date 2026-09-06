# Phase 6.2 — the experiment runner

**Completion update, 2026-09-06:** the first live experiment is complete. Read the
[results](phase-6.2-report.md) and [upload recovery lesson](evaluation-recovery.md).
The implementation-stage observations below describe the earlier runner increment.
Current readback also requires cloud tokens to match locally recorded actor/critic
usage before counting a root cost; two rows in the live baseline fail that accounting
check. Model/feedback recovery did not rerun the target or change baseline scores.

This lesson follows the [target](evaluation-target.md) and
[evaluator](evaluation-evaluators.md) increments. The theory and interface in
this section were written before the runner code. The runner connects those
pieces, preserves local evidence, and checks what LangSmith actually stores.

## Theory: an experiment needs an identity and a complete record

A target answers one task. An evaluator checks one result. An experiment runner
decides exactly which tasks to schedule, with which configuration, and collects
their results into one inspectable experiment.

There are three separate outcomes:

| Outcome | Question |
| --- | --- |
| Conversion | Did the graph finish with usable code, completed review, and no open TODOs? |
| Static evaluation | Which deterministic checks accepted the final artifact? |
| Experiment integrity | Did all scheduled rows and required feedback arrive, with matching evidence? |

Twelve failing conversions can still form a completely recorded experiment.
Conversely, eleven green results cannot establish a complete twelve-row run.
The report must preserve that distinction instead of giving the experiment one
ambiguous success flag.

The official SDK supports a target function, an iterable of dataset examples,
evaluator functions, experiment metadata, concurrency, and repetitions through
[`evaluate()`](https://docs.langchain.com/langsmith/evaluate-llm-application).
The runner will use our existing target and four evaluators with one concurrent
example and one repetition. Reflection stays at three total attempts; the
one-versus-three comparison belongs to 6.3.

## Pin a version before spending model calls

Using a dataset name alone selects whatever contents are current at read time.
Instead, load the verified 6.1 receipt and request examples at its recorded
timestamp using `list_examples(dataset_id=..., as_of=...)`. This is the documented
[dataset-version workflow](https://docs.langchain.com/langsmith/manage-datasets#evaluate-on-a-specific-dataset-version).

Materialize that response into a list once. Compare every example's ID, dataset
ID, inputs, reference outputs, and metadata with the locally preflighted snapshot.
Reject missing, unexpected, duplicated, or changed rows before `evaluate()`.
Pass the same checked objects to the SDK, avoiding a second dataset fetch that
could change what is actually run.

The local collection must also match the receipt's collection hash and twelve
deterministic example IDs. An old upload receipt cannot bless newly edited
fixtures. This runner reads the dataset; it does not update its examples.

## Record what is being measured

The plan records the selected model, maximum output tokens, critic effort and
structured-output policy, attempt cap, concurrency, and repetitions. It records
the git revision/dirty state, hashes of relevant source/playbook/tool configuration
and lock files, installed Python packages, Python/platform, Node, and installed
sandbox package versions. It never serializes `.env`, API keys, or headers.

Our learning agreement selects Opus for evaluation runs. The runner's default
will therefore be `anthropic:claude-opus-5`, with an explicit `--model` override
for future runs. This affects the standalone evaluation process; actor and
critic still use the existing `S2P_MODEL` mechanism. The normal converter's
default remains defined in `env.py`.

Recorded constructor settings are what the application requests. Actual provider
model identifiers, token accounting, and any service defaults must be interpreted
from the resulting child traces. Do not claim that a planned model string alone
proves a provider call occurred. Do not dump a whole model/client object to obtain
provenance: it can contain credentials.

`configuration_sha256` identifies the canonical JSON configuration. The dataset
has its own hash. Together they identify the intended task collection and
application configuration. This does not eliminate model randomness or provider
changes. Temporary absolute paths also still vary in prompts, as documented in
the target lesson.

The SDK can derive its own `dataset_version` from example modification times.
We additionally retain `pinned_dataset_version` under our own metadata key so the
requested snapshot timestamp remains explicit after the SDK updates its fields.

## Preserve work incrementally

Before running, write the plan and captured examples into a fresh artifact
directory. After the SDK creates an experiment, save its name, ID, and URL.
As each row finishes evaluation, append its run and feedback to a JSONL journal
and flush the file. A later exception must not erase earlier completed rows.

The final JSON report keeps full per-row outputs and feedback. Its readable
Markdown companion shows aggregate and per-row results and points to that raw
evidence. Missing rows remain explicit in the scheduled-row list and count as
zero verified passes. Duplicate or foreign rows are integrity errors, not extra
successes. Missing or duplicate required feedback is also an integrity error.

The runner catches ordinary execution exceptions at the experiment boundary and
still writes the partial report. A forcibly terminated process may leave only
the plan, experiment pointer, and completed-row journal; it cannot guarantee a
final report after the operating system terminates it.

## Read back the cloud evidence

An SDK call returning successfully does not prove every background upload has
arrived. Drain the client's tracing queue, then read the experiment, root target
runs, and required feedback. Compare remote inputs/outputs, example links, scores,
statuses, comments, and evaluator evidence with the local record.

In the installed SDK, `evaluator_info` is submitted as feedback-source metadata.
Verify those fields there; the existence of a green number alone is insufficient.
The server may add its own fields, so compare the fields we submitted while
retaining the full readback as evidence. Extra unrelated feedback does not replace
the eight required keys. Duplicate required keys remain an error.

A short bounded retry allows eventual consistency to settle. Failure to verify
stays explicit. A readback-only command should be able to retry verification
without rerunning paid conversions.

## Report the denominator and the costs honestly

For each gate, report verified passes divided by all scheduled examples, plus
counts of passed, failed, missing output, invalid input, tool error, and missing
feedback. Repeat those breakdowns by scenario and POM/test kind. Also report how
many rows passed all four static gates and how many graph reports were `passed`.

Preserve target and evaluator time separately, actor/critic usage, attempts,
TODOs, critique, refusal, and errors. Provider usage can be incomplete after a
failed request; missing values are unknown. Read available monetary cost from
LangSmith's root target runs, counting each root once. Do not add child costs to
an already aggregated root cost. If any row's cost is missing, the full total is
unavailable; a known subtotal and the number of unavailable rows can still be
reported. The SDK's reported cost is not an independently verified invoice.

The first runner verification will use the real SDK with fixed candidates and
uploads disabled, plus simulated cloud readback failures. That tests orchestration
and reporting. Only a later live converter experiment can supply model-quality
measurements and verified LangSmith URLs. Browser correctness remains outside
these four static metrics.

## The implemented files and their responsibilities

The implementation is split by responsibility so the dataset checks, execution,
reporting, and network readback can each be understood and tested independently.
Individual code patches stayed below 150 lines; the report module is longer
because its numeric reconciliation and Markdown rendering were delivered in
separate patches.

| File | Responsibility |
| --- | --- |
| [eval_plan.py](../src/selenium2playwright/eval_plan.py) | Prepare the pinned snapshot and configuration identity. |
| [eval_experiment.py](../src/selenium2playwright/eval_experiment.py) | Call the SDK, journal results, and save reports. |
| [eval_report.py](../src/selenium2playwright/eval_report.py) | Reconcile all scheduled examples and explain their results. |
| [eval_readback.py](../src/selenium2playwright/eval_readback.py) | Compare cloud evidence with the local record. |
| [run_eval_experiment.py](../scripts/run_eval_experiment.py) | Expose preview, live execution, and readback-only commands. |

### `digest`: identify a configuration

This function sorts JSON dictionary keys before hashing. Two dictionaries with
the same contents and different insertion orders therefore receive the same
identity. The hash changes when a recorded setting, file hash, or version changes.
It is a content identifier, not a certificate of code quality.

### `write_json`: save a complete artifact

Write formatted UTF-8 JSON to a temporary sibling file, then replace the target
file. A failed write before replacement cannot truncate an older complete JSON
artifact. The caller owns and creates the artifact directory. This function
does not create arbitrary directory trees or make cloud requests.

### `configuration`: capture the settings and tools

This function hashes Python source under `src/`, sandbox configuration/scripts,
the playbook, Python dependency files, and the runner script. It records installed
Python package versions and sandbox npm versions. The sandbox's dependencies are
pinned exactly; a mismatch between installed and declared versions stops planning.

Git revision and dirty state tell us whether the run corresponds to a clean
commit. File hashes identify the actual selected working-tree contents even when
changes are uncommitted. Hashes do not themselves preserve a restorable source
checkout; keep the corresponding code in version control before publishing a
reproducibility claim.

Only explicit model settings are recorded: actor/critic model, maximum output
tokens, critic effort, structured-output method, and unset temperature. Provider
defaults such as transport retries are not invented here; the versioned adapter
implementation and live traces remain relevant. Configuration is captured before
and after execution to detect changes during a run.

### `build_plan`: connect local evidence to the cloud receipt

First build the complete 6.1 collection using its existing fixture preflight.
Then compare the collection name/hash with the tracked upload receipt. Validate
that the pinned version has a timezone and that the receipt's example IDs and
verified count agree with the deterministic IDs generated for this collection.

Finally, package those examples with configuration and experiment metadata.
`expected_feedback_keys` contains eight keys: a numeric score and status for
each of the four gates. `models` is also supplied as an SDK metadata field, while
the full requested configuration stays available under `configuration`.

Planning creates no client and makes no model call. Imports follow the existing
application's environment-loading behavior; the plan never prints or serializes
the loaded credentials.

### `verified_examples`: check the one snapshot we will run

Read the named dataset by ID, verify its name/hash, and materialize its examples
at `dataset_version`. Reject any unexpected or duplicate ID, wrong dataset link,
changed input, changed golden output, changed metadata, or missing row.

The comparison explicitly includes `dataset_split: ["base"]`, matching the 6.1
uploader. Return the checked examples in the plan's stable case order. The SDK
gets these objects as `data`; it does not receive a name that would fetch the
dataset again. The SDK passes only each example's `inputs` to the target.

### `coherent_feedback`: decide whether a metric is usable

A returned feedback key is insufficient on its own. Require a known status, a
0/1 score that agrees with that status, the expected gate/evaluator version,
nonnegative finite elapsed time, and report/error fields.

For `passed` or `failed`, require a complete serialized `ValidationReport` with
matching gate and verdict and no evaluator error. For an unavailable verdict,
require a typed error message. A missing or contradictory report cannot receive
credit just because the numeric field says 1.

This is a contract check on the recorded evaluator evidence. It does not rerun
the validators a third time or prove that their rules cover every behavior.

### `measurement`: keep unknown measurements unknown

Convert available numeric values through decimal arithmetic, then return a
complete total only when every scheduled row supplied a value. Otherwise, retain
the known subtotal and count missing rows. JSON represents these decimal totals
as strings to preserve decimal arithmetic; `null` means unavailable.

For costs `[0.01, null, 0.02]`, the total is unavailable, the known subtotal is
`"0.03"`, and one row is missing. A reported zero is a known value; it is different
from a missing value. This function also handles target time and token totals.

### `aggregate`: count scores and measurements over a group

For each metric, count verified passes, retain the full group size as denominator,
and count every status category. Also count rows where all four static scores
are 1 and rows whose graph report says `passed`. These two numbers need not match.

The same function is used for the whole collection, each scenario, and each
POM/test kind. Target seconds and actor/critic total tokens have the same known/
unknown accounting as cost. Detailed cache/token metadata remains in row outputs;
the aggregate does not fabricate `total_tokens` when the provider omitted it.

### `assemble_report`: reconcile against what was scheduled

Group received records by example ID, then iterate the plan's examples, including
those with no result. Unexpected results and duplicate run IDs become integrity
issues. A scheduled example with zero or multiple result records receives no
verified-pass credit. The raw JSONL journal retains all received records.

For a single result, verify its example link and actual input snapshot. Require
each feedback key exactly once and use `coherent_feedback` to validate the score
and its evidence. Missing metrics become `missing_feedback`; contradictory or
duplicated metrics become `invalid_feedback`. Missing rows use `missing_result`.

Retain each row's output, feedback, run error, and available cost, then calculate
the whole-collection and grouped summaries. An execution exception also makes
local integrity incomplete. A compiler failure with correctly recorded feedback
does not by itself make experiment integrity incomplete.

### `render_markdown` and `save_report`: make the evidence readable

`render_markdown` shows the experiment mode, requested configuration, integrity
status, primary score table, scenario/kind table, and all scheduled conversions.
It then lists accounting totals and a detailed section for each row: stop reason,
notes, TODOs, critic, errors/refusal, usage, time, cost, and evaluator comments.

`save_report` writes both this Markdown and the full JSON report. The JSON
retains complete generated code, internal graph validation, external evaluator
findings, and raw tool output. The readable report does not silently truncate
those fields; it points to the full artifact instead of repeating large tool
payloads in every table.

### `run_experiment`: connect target, SDK, journal, and report

The main orchestration function performs these operations in order:

1. Create a fresh output directory and save the complete plan.
2. Check that a live run uses the real `conversion_target`, that the process's
   model matches the plan, and that configuration identity has not drifted.
3. Obtain the full verified dataset snapshot.
4. Call `evaluate()` with the target, checked examples, four evaluators, copied
   metadata, one concurrent example, and one repetition. `error_handling="log"`
   preserves target failures rather than asking the SDK to ignore them.
5. Save the experiment pointer and consume results as they become available.
   `blocking=False` lets the runner journal completed rows while the SDK continues.
6. Serialize each root run and its feedback, append it to `results.jsonl`, flush,
   and print the completed-row count. Only selected root-run fields are serialized;
   no whole model/client object is dumped.
7. Check configuration identity again, capture any ordinary execution exception,
   and save the local integrity/quality report.
8. For an uploaded run with complete local evidence, perform cloud readback and
   update the report with the verified outcome and available costs.

An offline test can inject a fixed target using `upload_results=False`. Live
mode rejects such injected targets, so this helper cannot accidentally publish
reference answers as a real converter experiment. Offline reports are labelled
`offline_sdk_check`; they establish orchestration behavior, not model quality.

The output directory must be new. Reusing one raises `FileExistsError` before
SDK execution. An ordinary execution failure leaves the plan, journal, experiment
pointer when available, and a partial report. A new run is a new experiment;
the runner does not silently retry paid conversions after a partial experiment.

### `readback_once`: compare one view of the stored experiment

Check the project ID, dataset association, and all submitted metadata. Fetch
root target runs using the current asynchronous `client.runs.query` API and
explicitly select IDs, example links, full inputs/outputs, errors, completion,
and cost. Consume the asynchronous iterator so pagination is handled by the SDK.

The current query API defaults to a one-day time window. Passing the saved
experiment start time prevents a later readback-only invocation from silently
omitting older runs. These API and field changes are described in the official
[run-query migration guide](https://docs.langchain.com/langsmith/smithdb-sdk-migration-query-runs).
The new runner does not use the deprecated `list_runs()` query method.

Require the exact root-run ID set and matching content, then require one matching
feedback item for each locally recorded key. Compare scores, categories, comments,
and every submitted evaluator-evidence field. Retain the complete project/run/
feedback readback and available root costs. A disagreement stays `unverified`.

### `verify_cloud`: allow bounded eventual consistency

Incomplete local evidence or a missing uploaded experiment ID prevents cloud
verification. Otherwise, flush the SDK trace queue and attempt readback up to
six times, with two seconds between unsuccessful attempts. Each attempt records
its issues and time; the final readback is retained in `cloud-readback.json`.

One `asyncio.Runner` owns the event loop across these retries, because the SDK
reuses asynchronous HTTP connections. A service error is reported explicitly;
it does not mark scores verified. Exhausting retries leaves an incomplete result
that can be checked again later.

### `verify_saved`: retry readback without repeating conversions

Load `report.json` and the completed-row journal, then call `verify_cloud`.
Persist the full cloud readback. After successful verification, associate reported
root costs with the corresponding local records and rebuild the grouped accounting.
Save updated JSON and Markdown reports. No target, model, or `evaluate()` call
is made on this path.

### Script `main`: select a concrete operation

The script defaults to a local plan preview. `--run` performs live conversion
and upload, and `--verify-only` checks an existing artifact directory. The two
network modes are mutually exclusive. The default model is Opus from the learning
agreement; `--model` changes only this evaluation process's actor/critic choice.

`Client` in SDK 0.12.1 does not itself implement the context-manager protocol.
The script uses `contextlib.closing(Client())` so each network path closes the
client even when an exception occurs. The offline integration tests exposed and
verified this compatibility correction.

## Commands and exit codes

Preview a fresh plan locally:

```bash
.venv/bin/python scripts/run_eval_experiment.py
```

Run the pinned dataset, creating a new experiment and a fresh artifact directory:

```bash
.venv/bin/python scripts/run_eval_experiment.py --run
```

Retry cloud readback for the actual artifact directory printed by a prior run:

```bash
.venv/bin/python scripts/run_eval_experiment.py --verify-only out/6.2/experiment-REPLACE-WITH-ACTUAL-DIRECTORY
```

`--output-dir` selects a fresh directory for a preview or live run. An existing
preview directory cannot be reused for live execution. `--model provider:model`
overrides the default model for a new preview/run.

| Exit code | Meaning |
| --- | --- |
| 0 | A preview was saved, or a verified live experiment has all static gates and graph reports passed. |
| 1 | Experiment evidence is complete and verified, but one or more static checks or graph reports did not pass. |
| 2 | Returned experiment evidence is incomplete or cloud verification is unsuccessful. |

Argument, credential, planning, or filesystem errors can stop the command before
an experiment begins and print an exception. They are not candidate-quality results.

## Verification and current checkpoint

The new runner/report tests cover 15 cases: configuration identity; missing,
duplicate, or changed pinned examples; a real SDK run over 12 fixed candidates;
preflight drift; an interrupted result iterator; exact cloud readback; missing or
corrupted cloud evidence; bounded read retries; saved readback without conversion;
rejecting injected live targets; missing-row denominators; malformed feedback;
duplicate/foreign results; graph-review versus static-pass status; and unknown
cost/token accounting.

The SDK integration uses the actual four validators and blocks HTTP requests.
It produced 12 completed rows and 96 feedback entries, saved matching JSON and
Markdown reports, and confirmed that an existing artifact directory cannot be
reused. Cloud verification tests use simulated service objects; they do not
establish that a live experiment has been uploaded.

The first focused test exposed the client context-manager incompatibility and
missing timestamps in the test's synthetic feedback objects. The client lifecycle
and test fixture were corrected. The resulting full offline suite passed:
**80 tests in 140.375 seconds**. Log: `out/6.2/runner-offline-tests.txt`.

```bash
.venv/bin/python -m unittest discover -s tests -v
```

The final local preview is
`out/6.2/preview-20260906T053636Z-a910cb7a/plan.json`: 12 scheduled examples,
96 expected feedback entries, requested model `anthropic:claude-opus-5`, three
total attempts, and configuration hash
`b66b194217b02ffcf679d9f7620308a9e9f6d0e77d8901233005d98997239451`.
Later source edits or commits can change the configuration identity; prepare a
fresh plan for the actual live invocation.

This runner increment is ready for review. No provider calls, browser runs, or
LangSmith writes occurred during it. Phase 6.2 remains in progress until the
first live converter experiment has verified scores and evidence in LangSmith.
Next: run the reviewed runner, inspect the real child traces and model/usage
details, verify cloud readback, and publish the evidence-backed 6.2 report.
Reported token totals can omit requests whose usage the provider did not return;
they are sums of recorded usage, not independently verified billing totals.
