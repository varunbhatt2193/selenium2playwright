# Phase 6.1 — dataset theory, first code increment, and reporting design

Status: the user approved committing the local snapshot builder and continuing.
It is pushed in `60fd0b5`; the coverage manifest is in
[evaluation-coverage.md](evaluation-coverage.md), and the approved alerts tests and
browser evidence are in [alerts-tests.md](alerts-tests.md).
Login and alerts fixtures are complete; four scenarios, the upload script,
target adapter, and experiments remain. No Phase 6 cloud dataset or scored experiment exists yet.

Phase 6 working rule: explain theory before writing each increment, put the
reasoning in code comments and docstrings, and walk through the implementation.
Keep code patches below 150 lines and preserve the user's review point. Reports
must expose detailed engineering evidence with an accessible explanation.

## What exactly are we measuring?

The system under test is the conversion graph: model, prompts, supplied context,
validation, critique, and allowed repairs. The dataset defines repeatable tasks
for that system. The experiment runner and evaluators come in Step 6.2.

One row means one file conversion. Our existing login test file contains two
browser tests, but produces one generated file and therefore one evaluation row.
Keep browser-case counts and conversion-row counts separate in every report.
Otherwise a statement such as "eight tests passed" has an ambiguous denominator.

In LangSmith terminology, an offline evaluation uses a prepared dataset rather
than production traffic. It can still call a live model and upload results.
Our current local snapshot checks use neither. See the official
[evaluation concepts](https://docs.langchain.com/langsmith/evaluation-concepts).

## The example contract

| Part | Fields in this increment | Who should receive it? |
|---|---|---|
| Inputs | `source_path`, `source`, `context_files` | Target application |
| Reference outputs | `code`, `expected_behaviors` | Evaluators and human reviewers |
| Metadata | Identity, scenario, kind, context policy, review record, fingerprint | Reporting and dataset management |

`source_path` is relative to the suite, such as `tests/login.spec.ts`.
`source` contains the Selenium text. `context_files` maps relative paths to
already-converted companion text. The reference output contains golden code
for the target file and a list of behaviors a valid conversion must preserve.
This uses LangSmith's documented
[example structure](https://docs.langchain.com/langsmith/manage-datasets-programmatically).

A path alone is not a frozen task: editing the checkout would change what the
next run reads. Snapshotting text preserves the selected input and reference.
The future adapter must reconstruct separate source and converted directories
from these snapshots before calling the current graph. Passing this dictionary
straight into today's graph would cause intake to reread local files instead.

Companion paths are relative to the converted tree, preserving imports such as
`../pages/LoginPage`. When evaluating `login.spec.ts`, its reviewed Playwright
page object is permitted context. The golden login test itself is excluded.
This is an isolated-file benchmark with supplied dependencies. Its result does
not establish that the converter can generate those dependencies correctly.

The builder rejects a target explicitly supplied as its own companion, including
through a symlink. Curation must still review companion contents and dependencies:
this check cannot detect an answer copied into a differently named regular file.
The target adapter must never pass the entire example, including outputs, to the model.

Goldens must be authored independently of the converter being evaluated, then
reviewed. A compiler pass cannot supply that review. The review status defaults
to `pending`; marking it `reviewed` requires a note identifying the review.
That note is a curator's record, not an automatic certification of correctness.
Changing a golden requires renewed review; a content hash does not grant it.

For login, acceptance criteria include both successful login and rejection of
invalid credentials. Equivalent variable names and code structure are acceptable.
A text diff helps inspection; exact code equality is not our correctness metric.

## Walk through the first implementation

The code lives in [eval_dataset.py](../src/selenium2playwright/eval_dataset.py).
It deliberately has no model client, LangSmith client, or graph dependency.

1. `DatasetCase` is a frozen dataclass: one curator-authored case definition.
   It holds identifiers, the suite-relative path, expected behaviors, companions,
   and reference review information. Tuples keep its collection fields immutable.
   Type hints document the interface; the builder checks important values at runtime.
2. `_read_typescript` resolves one explicit file beneath its suite directory.
   It rejects absolute paths, parent traversal, noncanonical aliases, wrong
   extensions, and symlinks that escape the suite. Missing and empty files fail
   visibly, rather than becoming empty evaluation examples.
3. `snapshot_example` checks the case definition, then reads the Selenium target,
   golden target, and declared golden companions. It returns ordinary dictionaries
   that can be serialized to JSON and later submitted to LangSmith.
4. Its `inputs` dictionary contains only source and permitted companions.
   Its `outputs` dictionary contains the answer and acceptance criteria.
   Metadata records how to interpret the example without adding prompt context.
5. `content_sha256` fingerprints inputs and outputs serialized with sorted keys.
   A source, reference, companion, or acceptance-criteria change changes that
   fingerprint. Dictionary insertion order does not. It identifies captured text
   and criteria; it excludes model settings, tool versions, and review metadata.

The returned dictionary is mutable, while its strings no longer depend on disk.
The caller must avoid altering an example after selecting it for an experiment.
Dataset-level duplicate IDs, review readiness, and repeatable upload behavior
belong in later curation/upload increments; they are not implemented here.

## The detailed LangSmith report we will build

The following is the reporting contract for later steps, not an existing report.
LangSmith supports configurable columns, metadata grouping, and trace inspection;
see [experiment analysis](https://docs.langchain.com/langsmith/analyze-an-experiment).

| View | Required evidence | Question it answers |
|---|---|---|
| Example | Source, companions, golden, actual code, acceptance criteria | What was the task and what was produced? |
| Gate results | Compile, residue, typed lint, parity; score and concrete findings | Which checked properties passed or failed? |
| Final outcome | Artifact present, TODOs, critic verdict, errors, stop reason | What still needs review? |
| Trace | Every conversion, validation, critique, and repair attempt | How did the final result arise? |
| Scenario groups | Scenario and file-kind counts, successes, failures, unavailable checks | Where does quality differ? |
| Efficiency | Attempts, wall time, actor/critic token usage, available cost data | What did this result require? |
| Reproduction | Dataset version, code revision, prompt hashes, tool versions, model settings | What configuration produced it? |
| Comparison | Per-example improvements/regressions, quality and resource deltas | Did the change help, and at what expense? |

Keep a compiler finding separate from an unavailable compiler. The main success
rate should use all scheduled rows as its denominator; show unavailable checks
and missing outputs explicitly. A secondary rate among successfully checked rows
must say so and report that smaller denominator. A POM has no browser test cases;
include separate test-file summaries so trivial POM parity does not obscure losses.

Compilation, lint, residue, parity, and the critic each have limits. Passing them
does not establish runtime behavior. Record browser execution as not measured
until it is actually performed. In Step 6.4, the independent judge needs its own
rubric, review calibration, and trace; the internal critic is not that judge.

For Step 6.3, compare one attempt with up to three total attempts on the same
dataset version and other settings. Preserve repetitions individually, count
both improvements and regressions, and distinguish percentage points from relative
change. These examples are a small curated development benchmark, not a random
sample proving future-suite success. Reserve new scenarios before later tuning.
LangSmith supports per-example inspection in its
[comparison view](https://docs.langchain.com/langsmith/compare-experiment-results).

LangSmith versions datasets as their examples change. Select and record an exact
version for comparisons; a dataset name alone can point to changing contents.
See [dataset versioning](https://docs.langchain.com/langsmith/manage-datasets).
Model/cost reporting must use actual recorded usage and verified pricing support;
missing cost information is unavailable, not zero. Separate judge evaluation
overhead from the application's actor/critic cost when comparing configurations.

## Evidence for this increment and the next review point

Local probes captured both existing login files as examples, validated the row
against installed LangSmith 0.12.1's `ExampleCreate`, checked JSON round-tripping,
and confirmed deterministic fingerprints and snapshot independence from disk edits.
Invalid paths, empty source, malformed review metadata, duplicate companions,
self-reference companions, and escaping/self-reference symlinks were rejected.
These are local probes, not new persisted regression tests or a scored experiment.
The existing offline suite also passed: 36 tests using
`.venv/bin/python -m unittest discover -s tests -v`.

The user approved continuing from this contract. The scenario coverage and
explicit case manifest are in [evaluation-coverage.md](evaluation-coverage.md).
Next, expand and review the source/golden pairs and build the upload script.
Step 6.1 finishes when the curated dataset is
visible and verified in LangSmith; Step 6.2 then runs the first scored experiment.
