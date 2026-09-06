# Phase 6.2 — deterministic evaluator theory and code

This is the second implementation increment of 6.2. The
[target adapter](evaluation-target.md) already runs one captured dataset input
through the converter. This increment adds the functions that grade its final
code. The theory and interface below were written before the implementation.
The [experiment runner](evaluation-runner.md) is now implemented; the first live
scored LangSmith experiment follows its review.

## What an evaluator does

Think of a target as the system under test and an evaluator as an assertion.
The target produces an actual result. The evaluator asks a specific question
about that result and returns a named measurement with supporting evidence.

LangSmith supports Python evaluator functions with named `inputs` and `outputs`
arguments, and a function may return several metrics. We use that to return a
numeric score and a separate status for every gate. See the official
[code evaluator guide](https://docs.langchain.com/langsmith/code-evaluator-sdk).
The return contract is also checked against the installed LangSmith SDK.

```python
def compiles(inputs: dict, outputs: dict | None) -> list[dict]:
    # Run the existing compiler against actual code and captured companions.
    # Return a score, a status, and the evidence explaining both.
    ...
```

`inputs` means the captured Selenium source and legitimate converted companions.
`outputs` means the actual dictionary returned by `conversion_target`.
Neither argument is the current row's reference answer. These static evaluators
do not need `reference_outputs`, because a valid conversion need not match the
golden file character for character.

## Four separate questions

| Function / score key | What earns 1 | What this does not establish |
| --- | --- | --- |
| `compiles` | TypeScript accepts the candidate and supplied companions under the sandbox configuration. | The browser performs the intended actions. |
| `residue_free` | The existing pattern scan finds no configured forbidden Selenium/Mocha/chai residue. | Every possible legacy pattern was detected. |
| `typed_lint_pass` | Typed ESLint reports no errors under the configured rules. | Every asynchronous or behavioral bug is absent. |
| `parity_pass` | The existing source/candidate comparison detects no tracked test or assertion loss. | Assertions have equivalent meaning; POM methods are equivalent. |

The roadmap asks for compilation, residue, and missing-await evaluation. The
typed lint gate already checks `no-floating-promises` plus other configured
error rules, so its metric name describes that broader scope. Warnings remain
visible but do not fail that gate. We also expose the existing fourth gate,
parity, so its evidence is available alongside the other three checks.

Parity on a POM containing no tests or assertions is a weak check: there may be
no test identities or assertion counts to compare. Report POMs and test files
separately when interpreting that metric.

## Why rerun checks after conversion

The graph runs gates internally to decide whether another repair is needed.
The evaluator runs those same implementations again on the final returned
`outputs["code"]`. It does not copy the graph's reported pass flags.

This matters because assembly can add TODO comments after the last validation,
and a failed repair can leave an earlier draft as the final artifact. An
evaluator should grade the exact artifact the caller receives. A copied or
stale success report must not make changed, broken code pass.

This is an independent execution, with the same rule limitations as the graph.
It adds compiler/linter time, but makes no additional model calls. The evaluator
does not ask the graph to repair failures discovered during grading.

## Reconstruct the candidate's import environment

For a test row, the files supplied to compile, residue, and lint look like:

```text
pages/LoginPage.ts    <- captured input companion
tests/login.spec.ts  <- actual returned candidate
```

Use the suite-relative names already stored in the snapshot. Before any
validator writes files, reuse `validate_inputs` to reject path escapes, aliases,
collisions, and malformed text. Copy the companion dictionary, then insert the
candidate under `source_path`; never modify the original snapshot.

Compile and lint already create and clean their own private sandbox workspaces.
The evaluator therefore needs no extra source directory. Residue scans the
supplied text. Parity receives only the matching source/candidate pair: the
companions have no source counterpart in this isolated conversion task.

A test row uses its supplied golden POM as legitimate context. Passing that row
does not demonstrate that a separately generated POM integrates with the test.
That remains a limit of the isolated-file benchmark.

## A score needs a reason

Every gate returns two metrics, such as `compiles` and `compiles_status`:

| Status | Numeric score | Interpretation |
| --- | --- | --- |
| `passed` | 1 | The gate ran and accepted the candidate. |
| `failed` | 0 | The gate ran and found a problem. |
| `no_output` | 0 | No non-blank candidate text was returned; the gate did not run. |
| `invalid_input` | 0 | The snapshot failed validation; the gate did not run. |
| `tool_error` | 0 | Checking could not produce a usable verdict. |

An unsuccessful tool result with no parsed findings is conservatively
`tool_error`: we cannot tell whether it reflects a candidate diagnostic or a
tool/parser failure. Preserve its raw output for investigation. Exceptions and
explicit `validator-error` findings also use this status. Ordinary lint warnings
do not become tool failures.

The primary metric measures **verified gate success per scheduled example**.
Zero means no verified pass; the status explains whether that came from bad code,
missing code, or unavailable verification. For example, eight passes, two code
failures, one missing output, and one tool error give `8 / 12 = 66.7%`. Dividing
by just the ten completed checks would hide the other two rows.

These functions return feedback for every call they receive. The future runner
must additionally reconcile scheduled rows and expected feedback keys: a process
that terminates before invoking an evaluator cannot be fixed by this function.
Do not assume that a dashboard average proves the denominator is complete.

The code's static score is separate from conversion success. A retained draft
can compile even if its repair or critic failed, or TODOs remain. Keep the
target's conversion/report status, errors, and TODO ledger beside these metrics.

## Evidence to retain in LangSmith

The numeric result will carry a readable comment and `evaluator_info` containing
the evaluator version, gate, status, local elapsed seconds, the complete
serialized `ValidationReport` when available, and a typed error otherwise.
That report preserves findings, locations, and raw tool output. A separate
status metric makes failure categories accessible without interpreting a zero.

Target time and evaluator time are different measurements: the target includes
conversion and its internal checks; the external evaluator times the additional
check. Neither measurement alone includes the entire cloud upload/readback.
The upcoming runner must verify what is actually stored remotely before making
claims about LangSmith visibility or completeness.

The earlier alerts negative control still defines the limit: changing Cancel
handling to OK can pass all four static gates while failing browser execution.
No score in this increment is named behavioral correctness.

## Walkthrough of the implementation

The implementation is in
[eval_evaluators.py](../src/selenium2playwright/eval_evaluators.py).
It is 110 lines, including explanatory comments, docstrings, and spacing.

### `gate_feedback`: turn evidence into LangSmith feedback

This helper receives the gate, status, start time, and any available report or
error. `GATE_KEYS` maps an internal name such as `compile` to its public metric
name, `compiles`. Keeping this mapping explicit makes the experiment columns
predictable.

The readable comment starts with the status. `report.render()` adds the gate
summary and individual findings, including file/line/column when known. An
error adds its type and message. This gives a reader a useful explanation
without interpreting the raw compiler or ESLint output first.

`report.model_dump(mode="json")` converts Pydantic objects to JSON-compatible
values. It preserves the complete findings and `tool_output`; it does not
replace evidence with a pass flag. The `evaluator_info` dictionary also records
the version `deterministic-v1` and elapsed local time. That version describes
the scoring policy; the runner must separately capture actual code and tool
configuration identities.

The first returned dictionary uses `score`, with `int(status == "passed")`
producing 1 or 0. Its feedback configuration describes a numeric range from 0
to 1. The second uses `value` for a status string and appends `_status` to the
metric key. For example, a compiler timeout produces:

```python
[
    {"key": "compiles", "score": 0, "comment": "tool_error ...",
     "evaluator_info": {"status": "tool_error", "error": {
         "type": "TimeoutExpired", "message": "..."}, "report": None}},
    {"key": "compiles_status", "value": "tool_error", "comment": "tool_error ..."},
]
```

This is an abbreviated shape illustration, not a recorded cloud result.
The status entry repeats the readable comment; the full structured report is
attached to the numeric entry to avoid storing it twice in the returned list.

### `evaluate_gate`: run one check against the actual candidate

This is the shared execution path for all four public evaluators:

1. Start the local timer and validate the snapshot. Invalid snapshots return
   `invalid_input` feedback before invoking a validator.
2. Read only `outputs["code"]`. Missing, non-string, or blank text returns
   `no_output`. If both input and output are invalid, input validation wins.
3. Create `candidate = {source_path: code}` and combine it with a copy of the
   captured companion dictionary. This preserves imports and leaves the dataset
   unchanged.
4. Select one existing validator. The small lambdas delay each call, so asking
   for compilation does not also execute lint, residue, and parity. Parity
   receives the matching source/candidate dictionaries without companions.
5. Validate the returned object as a `ValidationReport` and check its gate name.
   A malformed return or a report for the wrong gate becomes `tool_error`.
6. Treat an unsuccessful report without findings, or an explicit
   `validator-error` finding, as unusable verification. Retain the report and
   raw output for diagnosis.
7. Otherwise honor `report.passed`. In particular, warning-only lint findings
   can coexist with a score of 1 under the existing severity policy.
8. Convert ordinary exceptions into `tool_error` feedback. An exception in one
   wrapper does not stop the caller from invoking the remaining evaluators.
   `KeyboardInterrupt` and `SystemExit` are not swallowed.

The helper ignores the target's reported success flags. Consequently, a candidate
with `report.status="needs-review"` can still score 1 for compilation, while a
candidate accompanied by a stale `passed` report can score 0. That is intentional:
artifact properties and completion of the conversion process answer different
questions.

### Four small public wrappers and `EVALUATORS`

| Function | One-sentence explanation |
| --- | --- |
| `compiles` | Runs TypeScript on the returned code and its supplied helper files. |
| `residue_free` | Searches those files for the old Selenium, Mocha, and chai patterns our rules recognize. |
| `typed_lint_pass` | Checks those files for configured lint errors, including forgotten awaits. |
| `parity_pass` | Compares the source and returned file for lost tests or assertions. |

Each wrapper selects its gate through `evaluate_gate`; the shared failure and
feedback policy therefore stays consistent. The `inputs` and `outputs`
parameter names are required by the SDK's argument binding convention.

`EVALUATORS` lists these functions in compile/residue/lint/parity order. The
upcoming runner can pass it to `evaluate(evaluators=EVALUATORS)`. Each wrapper
returns two metrics, yielding eight feedback keys per evaluated row.

## Verification of the evaluator itself

An evaluator is code and can contain bugs. Testing it only with good code would
miss a function that always returned 1. The 11 tests in
[test_eval_evaluators.py](../tests/test_eval_evaluators.py) deliberately include
valid candidates, injected defects, infrastructure failures, and known blind spots.

| Test | Evidence established |
| --- | --- |
| Golden login POM and test | All four real validators pass; companion imports work; inputs are unchanged and evidence serializes. |
| Misspelled `.fil()` with a stale success report | The actual compiler returns a TypeScript diagnostic and score 0. |
| Missing `await` | Compilation passes while typed lint detects `no-floating-promises`. |
| Legacy source / deleted assertion | Residue detects a forbidden import; parity identifies the affected test and assertion loss. |
| Missing / broken POM companion | The compiler exposes the missing import or companion diagnostic. |
| Wrong Cancel behavior | All four static evaluators pass the known behavioral defect; no browser result is invented. |
| Missing candidate / invalid snapshot | All wrappers return explicit zero feedback without calling a validator. |
| Missing compiler / timeout / wrapper exception | Each produces typed error evidence; another evaluator still runs. |
| Invalid, mismatched, or unusable reports | None becomes a candidate pass, and available raw evidence is retained. |
| Lint warning | Score remains 1 while its location, comment, and raw details remain visible. |
| Installed SDK adapter | `run_evaluator(...).evaluate_run(...)` binds the actual inputs/outputs, preserves evidence, and returns eight distinct keys. |

The SDK adapter check uses a deliberately unusable reference answer, fixed gate
reports, and disabled tracing. It also verifies exact candidate/companion file
arguments and parity's source pair. It exercises local SDK compatibility without
creating a LangSmith experiment. The installed SDK is **0.12.1**.

Focused verification passed: **11 tests in 5.553 seconds**. Run it with:

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_eval_evaluators.py' -v
```

These are evaluator regression checks, not a measured model conversion rate.
No provider calls or browser executions were needed for this increment.

The full offline suite passed: **65 tests in 41.079 seconds**. Its local log is
`out/6.2/evaluator-offline-tests.txt` (ignored generated evidence). Reproduce with:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## Current checkpoint and next increment

The target adapter and evaluator wrappers are implemented; this evaluator
increment is ready for review. No scored experiment has been created by this
work. Phase 6.2 remains incomplete until the first experiment has verified scores
in LangSmith.

The [runner](evaluation-runner.md) now uses the pinned 6.1 dataset and these four
evaluators. Its report reconciles all 12 scheduled examples and all eight feedback keys
per row, verifies remote evidence, and includes the configuration, scenario/kind
breakdowns, attempts, TODOs, critic/errors, latency, usage, and available cost
described in the [target reporting plan](evaluation-target.md#detailed-reporting-required-for-the-first-experiment).
Unknown costs must remain unavailable. The static results and any future browser
results must each retain their stated scope.
