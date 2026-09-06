# Phase 6.2, part 1 — connect a dataset row to the converter

This increment builds the **target adapter**. It prepares one captured input for
the existing graph and returns its actual conversion evidence. The evaluator
functions and first scored LangSmith experiment follow in the next increment.

## Theory before code

Think of a data-driven automation test: test data describes the input, a driver
executes the application, and assertions judge the result. Our evaluation has
the same three responsibilities:

| Piece | Responsibility in this project |
| --- | --- |
| Dataset example | Captured Selenium source, supplied dependencies, and a separately stored reference answer. |
| Target function | Run the converter on the captured input and return what happened. |
| Evaluator function | Inspect the returned conversion and produce a named score with evidence. |

LangSmith accepts a target with the interface `target(inputs: dict) -> dict`;
`evaluate()` calls it for examples and traces its execution. Our graph needs an
adapter because its file-path input contract differs from the dataset's captured
text contract. See the official [target-function guide](https://docs.langchain.com/langsmith/define-target-function).

Evaluators can receive `inputs`, the target's `outputs`, and the dataset's
`reference_outputs`. These are separate arguments with different meanings: the
target's output is the attempted answer; the reference is the curated answer.
See the official [code-evaluator guide](https://docs.langchain.com/langsmith/code-evaluator-sdk).

### Why passing the dataset directly to the graph is wrong

The stored `source_path` is a suite-relative identity such as
`tests/login.spec.ts`. It is not a promise that a file exists under the current
working directory. The source text captured in 6.1 defines the task, even if the
developer later edits the corresponding sample in the checkout.

Our graph's `intake()` reads `source_path` and `context_paths` from disk. Passing
only the stored relative path would either fail to find a file or read unrelated,
possibly changed contents. Passing the whole example would also mix reference
answers and metadata into the application's input boundary.

The adapter therefore accepts **exactly** these input fields:

```python
{
    "source_path": "tests/login.spec.ts",
    "source": "<captured Selenium test text>",
    "context_files": {"pages/LoginPage.ts": "<supplied Playwright POM text>"},
}
```

The target file's golden code and expected-behavior list remain with the
evaluators. A supplied golden POM is intentionally part of the task for a test
row: this benchmark measures isolated conversions with known dependencies.
Converting every POM and then using those generated POMs in the tests would
measure a different, dependent suite-conversion task.

### Why the temporary folder has two trees

For the login test, the adapter creates this layout:

```text
<unique temporary directory>/
  source/
    tests/login.spec.ts       captured Selenium input
  converted/
    pages/LoginPage.ts        supplied Playwright dependency
    tests/login.spec.ts       intended output location; initially absent
```

The source and destination can share a relative filename without overwriting
each other. The intended output path anchors `../pages/LoginPage` next to the
converted dependency. The graph's validators already know how to copy that
relative layout into their TypeScript checking workspace.

The graph returns generated code in memory; it does not need the adapter to
write an output file. A fresh folder per invocation keeps rows independent.
The `with TemporaryDirectory(...)` block removes its files when execution ends,
including when an exception interrupts it. This isolates files; it is not a
security boundary for running arbitrary generated programs. This increment
does not execute generated browser tests.

### Why failure belongs in the output contract

An evaluation must distinguish “produced code” from “passed review.” The graph
uses `converted` to mean that a draft exists. Its assembled report uses `passed`
or `needs-review` after considering gates, the critic, TODOs, and errors.

A refused input has no report because that graph branch ends before assembly.
A provider failure handled by the graph produces an honest report with no code,
or preserves the previous draft if a repair failed. An unexpected exception that
escapes the graph produces an adapter error. We must retain all these outcomes
so a later experiment cannot improve its apparent score by dropping failed rows.

We cannot recover an interrupted graph's final attempt count or partial usage
from an `invoke()` call that never returned. Such evidence remains unavailable;
the adapter does not invent a zero or construct a passing report.

## Implemented interface

```python
def validate_inputs(inputs: dict) -> None:
    """Reject malformed snapshots before creating files or invoking the graph."""

def materialize_inputs(inputs: dict, workspace: Path) -> dict:
    """Write only captured inputs and return the graph's file-path arguments."""

def conversion_target(inputs: dict) -> dict:
    """Run one isolated conversion and return JSON-compatible evidence."""
```

Implementation: [eval_target.py](../src/selenium2playwright/eval_target.py),
91 lines including explanatory comments. Each code patch was below 150 lines.

## Walkthrough of every function

### `validate_inputs(inputs)` — check that the snapshot can be used as a task

1. Require a dictionary containing exactly `source_path`, `source`, and
   `context_files`. Rejecting unknown fields catches accidentally supplying the
   whole example or adding `reference_outputs` to the target input.
2. Require `context_files` to be a dictionary of relative paths and text. An
   empty dictionary is valid for a standalone POM conversion.
3. Examine the source and companions together, without reading local samples.
4. Require canonical, relative `.ts` paths. Absolute paths, parent traversal,
   backslashes, drive-like colons, NUL characters, and aliases such as `./a.ts`
   are rejected before a snapshot file is written.
5. Require non-blank strings for source and companion contents. Empty data
   cannot silently become an easy conversion case.
6. Detect path collisions, including case variants and a file being used as
   another file's parent directory. Including the target path prevents its own
   reference from being supplied under that same companion identity.

This is structural validation, not semantic proof that arbitrary supplied text
contains no answers. Curation and collection verification still establish which
companions belong in the benchmark. The target does not reverify the cloud
dataset's version or collection hash; the experiment runner will do that once
for the selected dataset before scheduling conversions.

### `materialize_inputs(inputs, workspace)` — turn captured text into graph inputs

1. Validate the entire input before writing a source or companion file.
2. Write the source text beneath `workspace/source/`, using UTF-8.
3. Write each supplied companion beneath `workspace/converted/`. Sorting paths
   makes companion order stable even if dictionary insertion order changes.
4. Return absolute paths when given the absolute temporary workspace used by
   `conversion_target()`. `source_path` identifies the Selenium input;
   `context_paths` identify converted dependencies; `output_path` identifies
   where the generated file belongs relative to those dependencies.

The caller must supply a fresh, owned workspace. This helper is not designed to
write into an arbitrary existing project that might contain symlinks or files
with the same names. The public target satisfies that requirement by creating a
new temporary directory for each invocation.

### `conversion_target(inputs)` — invoke the graph and return its evidence

1. Start `perf_counter()`, a monotonic elapsed-time clock. It measures this
   invocation's local preparation, graph execution, serialization, and cleanup;
   it does not measure later evaluator work or LangSmith upload time.
2. Initialize a complete output shape with absent code/report/usage represented
   by `None`. A missing result must never look like an empty file that passed.
3. Create the temporary directory, materialize the captured inputs, and invoke
   the existing graph. The graph retains its current three-attempt cap; this
   increment does not add the configurable comparison planned for 6.3.
4. Give the graph trace the name `evaluation-conversion` and tags `step:6.2`
   and `target:v1`. The recursion limit matches the graph CLI's budget formula.
5. Serialize the assembled Pydantic report using `model_dump(mode="json")`.
   This retains nested findings, raw tool output, the critic's fixes, conversion
   notes, TODOs, attempts, errors, and the stop reason as JSON-compatible data.
6. Copy `code` from that assembled report for convenient evaluator access.
   Keeping code at the top level is deliberate duplication; the report remains
   the complete artifact. A retained draft from a failed repair remains visible.
7. Handle refusal separately, since the graph's refusal branch has no assembled
   report. Return the explicit refusal reason and no generated code.
8. Capture an exception that escapes the graph as a typed `adapter_error`.
   The broad `Exception` catch is at the evaluation boundary so an ordinary row
   failure can be scored and reported. It does not catch `KeyboardInterrupt` or
   `SystemExit`, which are outside `Exception` in Python's hierarchy.
9. Record elapsed time and return the evidence after temporary-file cleanup.

## Output contract and how to read it

| Field | Meaning |
| --- | --- |
| `code` | Latest assembled TypeScript draft, or `null` when none is available. |
| `conversion_status` | Graph outcome `converted`, `failed`, or `refused`; `error` means an exception escaped to the adapter. |
| `report` | Complete assembled report, or `null` for refusal or unavailable final state. |
| `report.status` | `passed` or `needs-review`; only meaningful when a report exists. |
| `report.attempts` | Conversion attempts started, including a failed provider attempt. |
| `report.validation` | Final gate reports, including findings and raw tool output. |
| `report.critique` | Final critic verdict/fixes, or `null` if unavailable. |
| `report.result` | Code, conversion notes, and consolidated TODO ledger. |
| `report.errors` | Errors already handled by the graph, such as provider/critic failure. |
| `refusal` | Why an unsupported input was not converted; empty otherwise. |
| `usage`, `critic_usage` | Graph-recorded actor/critic token totals across attempts, including supplied cache details; `null` means unavailable. |
| `adapter_error` | Escaped exception type and message; `null` when the adapter completed normally. |
| `elapsed_seconds` | Local target duration, including its graph execution and cleanup. |

For example, `conversion_status="converted"` with
`report.status="needs-review"` is valid: code exists, but findings, TODOs, an
unavailable review, or a failed repair prevent a complete pass. Likewise,
`conversion_status="failed"` with `adapter_error=null` means the graph handled
the conversion failure and returned a report; it does not mean the row succeeded.

Only final review/gate evidence and cumulative usage are in this output. Earlier
attempts are visible through graph traces when tracing is enabled. The adapter
does not turn the final report into a full attempt-by-attempt history.

Temporary absolute filenames currently appear in graph inputs and prompts,
because the existing graph uses disk paths as prompt labels. They vary between
runs. The relative task identity and captured contents remain stable; future
reports must retain actual traces and describe the same materialization policy
instead of claiming that every rendered prompt byte is identical.

## Verification and its limits

The seven tests in [test_eval_target.py](../tests/test_eval_target.py) cover:

| Check | Evidence established |
| --- | --- |
| All 12 dataset snapshots | Exact supplied source/companions reach intake, including a captured edit absent from the checkout; only input files are created. |
| Invalid input cases | Bad paths, empty text, incorrect shapes, extra answer fields, and self-companions fail before graph invocation. |
| Login POM and login test integration | Fixed replies traverse the real graph and all four actual validators; the first actor prompt contains source and excludes the complete target reference. |
| Unsupported source | The real refusal branch returns a reason without a model call. |
| Initial provider failure | The real graph retains its failed, one-attempt report with no code. |
| Retained report serialization | An assembled draft, findings, raw tool output, TODOs, review, attempts, and nested token usage survive the adapter unchanged. |
| Escaped graph exception | The error remains visible, unknown usage remains `null`, and separate temporary folders are removed. |

Run the focused checks:

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_eval_target.py' -v
```

They passed: **7 tests in 2.440 seconds**, with tracing disabled and fixed model
replies. The first test run exposed an error in the test double: provider message
preparation can return a message list rather than a `PromptValue`. The capture
helper now handles both forms. No production graph change was needed.

The full offline regression suite also passed: **54 tests in 37.409 seconds**.
Its local log is `out/6.2/target-offline-tests.txt` (ignored generated evidence).
Reproduce with `.venv/bin/python -m unittest discover -s tests -v`.

These are adapter checks, not a measured model conversion rate. There were no
provider requests, browser runs, scored LangSmith experiments, or cloud writes
in this increment. The previously uploaded dataset and its pinned version remain
the basis for the forthcoming experiment.

## Detailed reporting required for the first experiment

The [deterministic evaluator increment](evaluation-evaluators.md) now wraps the
four checks and retains score/status/diagnostic evidence. The
[runner that calls `evaluate()`](evaluation-runner.md) is also implemented;
the first live experiment is next. Its report must include the following evidence, with
unknown values explicit:

- Experiment URL, dataset ID and pinned version, expected/scheduled/completed
  row counts, and collection identity.
- Code revision and dirty state; prompt, playbook, schema, and tool configuration
  identities; actual model/provider settings and allowed attempts.
- Per-row source identity, scenario/kind, output, gate scores and findings,
  conversion/report statuses, TODOs, critic result, and error category.
- Attempts, target/evaluator durations, actor/critic usage and available cost.
  Missing cost is unavailable, never an inferred zero.
- Aggregate scores using all scheduled examples, with scenario and POM/test
  breakdowns. Tool failure, unavailable output, and refusal remain visible.
- Scope: static checking does not establish correct browser behavior. The
  alerts Cancel/OK negative control remains the concrete demonstration.

The target adapter is implemented. Phase **6.2 remains in progress** until
the first live experiment exists with verified scores in LangSmith.
