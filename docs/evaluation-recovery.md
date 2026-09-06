# Recovering an experiment without changing the experiment

## Theory: execution and delivery are different operations

The converter produces an answer locally. The evaluator checks that answer locally.
LangSmith receives traces and feedback over a network. These are separate operations:
a successful conversion does not guarantee successful delivery of its evidence.

Our first live experiment made this concrete. All 12 rows finished and the journal
contained 96 feedback entries. LangSmith initially returned only 11 root records and
72 feedback entries. One root was still unfinished. A background multipart request
reported HTTP 400: the submitted numeric feedback definition had `min=0, max=1`,
while the stored definition had neither bound. The scores themselves were valid.
The evidence establishes the conflicting definitions; it does not establish why
the stored definitions acquired those values.

The journal is useful because it records the actual answer, errors, timestamps,
usage, and evaluator evidence before cloud verification. Repeating the converter
would produce a different experimental observation, consume more tokens, and risk
replacing a failure with a lucky second answer. Restoring delivery preserves the
original observation, including failures.

The recovery unit is one missing root, one unfinished root, or one missing feedback
entry. It is **not** a new model attempt. Final code cannot reconstruct a missing
model reply, prompt history, or intermediate reasoning; those traces stay missing.

## Theory: preflight, identity, and repeatability

Before the first write, compare the entire local journal with current cloud state.
The dataset, experiment, configuration, input, and example identity must agree.
Existing feedback must agree on score, status, comment, and evaluator evidence.
A conflict anywhere stops planning before any item is written.

Only absent evidence or an empty unfinished root can be repaired. Completed
conflicting output is never overwritten. Feedback IDs are derived deterministically
from the original run UUID and metric key. If a network response is lost after the
server accepts a write, the next inspection finds that record and skips it. The
stable ID also prevents an ambiguous retry from becoming a different feedback item.
This is repeatable recovery, not a transaction spanning the whole experiment: an
interruption may leave some writes complete, so each acknowledged action is journaled.
Do not run concurrent recovery processes for the same experiment.

The original `results.jsonl` stays unchanged. Each recovery invocation gets a new
directory containing the plan and cloud snapshot. An apply invocation also retains
the previous report/readback and an append-only list of acknowledged actions. A
SHA-256 hash links recovery provenance to the exact original journal bytes.

## Theory: a score, a status, and a metric definition

`score=0` is a measurement. `value="no_output"` explains the measurement. A
`feedback_config` describes the shared metric key; it is not needed to calculate
either value. Sending that definition on every result caused the observed conflict.

`gate_feedback()` now emits the same 0/1 scores and status strings while omitting
`feedback_config`. The existing `int(status == "passed")` expression and
`coherent_feedback()` checks still enforce the binary scoring policy. Nothing in
the model, playbook, repair loop, or evaluator verdict changes. The evaluator remains
`deterministic-v1`; the source/configuration fingerprint changes for future runs.
We do not rewrite workspace metric definitions to accommodate this experiment.

## Code: `eval_recovery.py`

`recovery_actions(report, records, cloud)` returns a reviewable list of missing
items and raises `ValueError` for inconsistent evidence. It reruns the normal local
report reconciliation rather than trusting a previously saved `complete` flag.
It then checks project metadata, duplicate/unexpected roots, original root identity,
timestamps, inputs, completion state, and feedback. It performs no network writes.

`apply_action(client, action, report, records, journal_sha256)` writes one planned
item using the original journal values. A missing root keeps its UUID, trace UUID,
reference example, original times, inputs, outputs, and error. Its `dotted_order`
uses the SDK's UTC timestamp + UUID convention so existing children retain their
relationship to the restored root. An unfinished root receives only its saved
completion/output. Feedback keeps its original evaluator evidence and adds a
separate `upload_recovery` provenance object. The apply receipt records every
operation, including finishing a root without changing its metadata.

The feedback call omits `feedback_config`. It supplies experiment `session_id` and
root `start_time` for the SDK's current feedback API. The first recovery succeeded
without those two fields but emitted a deprecation warning; the helper now supplies
them for future use. Other SDK legacy calls have not been comprehensively migrated.

## Code: the command-line recovery and inspection helpers

`recover_eval_upload.main()` reads the saved journal, hashes its bytes, asks LangSmith
for current evidence, and saves the complete repair plan. Its default is preview.
`--apply` executes the preflighted actions with `auto_batch_tracing=False`, making
upload errors visible at the individual call instead of a background batch. It
does not import or invoke a target/evaluator as part of recovery execution.

```bash
.venv/bin/python scripts/recover_eval_upload.py out/6.2/experiment-20260906T055741Z-f4465fd0
.venv/bin/python scripts/recover_eval_upload.py out/6.2/experiment-20260906T055741Z-f4465fd0 --apply
.venv/bin/python scripts/run_eval_experiment.py --verify-only out/6.2/experiment-20260906T055741Z-f4465fd0
```

`inspect_eval_traces.inspect(client, folder)` uses the paginated current run-query
API to capture all trace nodes and the experiment's metric definitions in ignored
`trace-audit.json`. This is a read-only diagnostic. `inspect_eval_traces.main()`
parses the artifact directory and closes the client after the asynchronous query.
The API returned lowercase `llm`; the inspection counter normalizes case.

```bash
.venv/bin/python scripts/inspect_eval_traces.py out/6.2/experiment-20260906T055741Z-f4465fd0
```

`publish_eval_report.main()` previews a checked-in narrative, then with `--publish`
rechecks the experiment's row evidence, writes that narrative into its description,
and reads it back exactly. It preserves the experiment name and metadata. Per-row
model outputs and evaluator diagnostics remain accessible in the experiment table.

## Code: accounting when some traces are missing

`readback_once()` now also requests root `TOTAL_TOKENS`. Before using a root's cost,
it requires local totals for both actor and critic and checks their sum against the
cloud root aggregate. A missing role total or a mismatch makes that row's usable
cost unavailable. Raw cloud cost and the comparison remain in `cost_coverage`.

In this baseline, WindowsPage has 4,270 recorded actor tokens but an unfinished
cloud actor trace showing zero; the Windows test has 9,051 recorded tokens but only
its 4,723-token critic trace persisted. Their root costs cannot stand for complete
row costs. Ten other roots match recorded usage. Their known subtotal is $0.518773;
the experiment's complete monetary cost remains unavailable. We never sum child
costs on top of an already aggregated root cost or treat unknown usage as zero.

Matching totals is an accounting consistency check, not proof that every graph
span persisted or an independent validation of provider billing. Root/feedback
verification and complete child-trace retention are separate claims.

## Verification and what this incident teaches

Five recovery tests cover no-op repetition, stable IDs and original payloads,
finishing an empty root, global preflight rejection of conflicts/duplicates, and
local journal corruption. The evaluator SDK tests ensure no metric definition is
sent. A readback regression rejects partial/unknown token accounting without
changing quality scores. The full offline suite passed **86 tests in 57.434s**.

Live recovery acknowledged 26 writes: one root create, one root finish, 24 feedback
creates. Readback verified 12 roots and 96 feedback entries. Repeating preflight
returned zero actions. The journal hash remained unchanged. No model or evaluator
was rerun during recovery, and no original failure was removed.

Chrome showed the restored WindowsPage row with four `0.00` scores and four
`no_output` statuses. Its summary still showed `1.00 AVG` and `92%` evaluated after
refresh, despite complete row-level API evidence. We retain this discrepancy;
we have not established its cause or changed scores to manipulate the display.
Use the reconciled 11/12 scorecard and the per-row evidence for this baseline.

Continue with the [phase 6.2 report](phase-6.2-report.md) for the experimental
findings, retained limitations, exact identifiers, and the next learning step.
