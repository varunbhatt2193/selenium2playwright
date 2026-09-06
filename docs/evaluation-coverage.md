# Phase 6.1 — coverage matrix and case manifest

Status: Phase 6.1 complete. All 12 source/golden pairs are curated and verified;
the dataset is uploaded, read back exactly, and visible in LangSmith. This page
explains the coverage plan; measured evidence is in [evaluation-fixtures.md](evaluation-fixtures.md)
and the [completion report](phase-6.1-report.md). Converter scoring starts in 6.2.

## Theory: select the cases before measuring the converter

A coverage matrix connects each scenario to the behavior we need to preserve.
The manifest expresses that matrix as data the evaluation tooling can consume.
For an SDET, this is the bridge between a test plan and data-driven execution.
The snapshot builder answers "what contents belong to one example?"; the manifest
answers "which examples belong in this benchmark, and why?"

An explicit list makes omissions reviewable. A directory scan would identify
files, but would not explain their expected behavior, review history, or role in
coverage. We will deliberately update the list when adding or retiring a case.

The current graph converts one file per invocation. Six page objects plus six
test files therefore mean 12 conversion examples. Two of those test files each
contain two planned browser tests; the remaining four each contain one. The
planned browser-test total is eight. Neither count represents measured passes.

The roadmap asked for roughly five POMs. This plan uses six, one per scenario,
to keep dependency relationships and scenario-level reports easy to interpret.

## The planned matrix

Each scenario has a standalone POM row and a test-file row that receives that
POM's reviewed Playwright version as context. Paths below are suite-relative;
the same paths must exist beneath both sample suites before snapshotting them.

| Scenario and reference page | POM | Test file | Planned browser tests | Current state |
|---|---|---|---:|---|
| [Login](https://the-internet.herokuapp.com/login) | `pages/LoginPage.ts` | `tests/login.spec.ts` | 2 | Existing, reviewed |
| [Alerts](https://the-internet.herokuapp.com/javascript_alerts) | `pages/AlertsPage.ts` | `tests/alerts.spec.ts` | 2 | Existing, reviewed, browser-verified |
| [Iframe](https://the-internet.herokuapp.com/iframe) | `pages/IframePage.ts` | `tests/iframe.spec.ts` | 1 | Agent-curated, browser-verified |
| [Windows](https://the-internet.herokuapp.com/windows) | `pages/WindowsPage.ts` | `tests/windows.spec.ts` | 1 | Agent-curated, browser-verified |
| [Upload](https://the-internet.herokuapp.com/upload) | `pages/UploadPage.ts` | `tests/upload.spec.ts` | 1 | Agent-curated, browser-verified |
| [Dynamic loading](https://the-internet.herokuapp.com/dynamic_loading/2) | `pages/DynamicLoadingPage.ts` | `tests/dynamic-loading.spec.ts` | 1 | Agent-curated, browser-verified |

1. Login preserves successful authentication and rejection of an invalid password.
   These are the two existing tests. Review approval for their goldens was recorded
   on 2026-09-04 in local `roadmap.md`, Step 1.3; the manifest carries that provenance.
2. Alerts preserves two different actions: accepting a simple alert and dismissing
   a confirmation. The tests must verify the resulting messages. A conversion that
   clicks the correct button but chooses the wrong dialog action is still wrong.
3. Iframe reads the initial editor content inside the frame, then checks the parent
   heading. This tests correct frame targeting and subsequent parent-page access.
   It does not exercise editor typing; the earlier typing failure in
   [gap-log.md](gap-log.md) remains an explicit coverage gap, not a resolved defect.
4. Windows checks content in the newly opened window, closes it, and confirms the
   original page remains usable. Asserting only against the original page would
   miss the central behavior, even if the converted code compiled.
5. Upload creates its own small local fixture, uploads it, checks confirmation and
   the exact filename, and cleans up. Generating the fixture within each test avoids
   an unstated dependency on a developer's filesystem. Fixture creation/cleanup
   are present in both source and golden.
6. Dynamic loading uses Example 2, whose page describes an element rendered later.
   The test must start loading, wait for completion, and check the resulting text.
   Successful compilation alone cannot establish that the timing was preserved.

The page references establish the targets for curation; visiting their initial
content does not verify these planned interactions. Exact locators, messages,
timing, and browser behavior must be checked when implementing the sample pairs.

## Code walkthrough

Read [eval_manifest.py](../src/selenium2playwright/eval_manifest.py). It imports
the existing `DatasetCase` and defines three constants. There are no new functions,
model calls, uploads, or file reads during import.

- `PLANNED_BROWSER_TEST_COUNTS` maps each test-file case ID to its intended number
  of browser tests. It is a planning target, not an automatic count of source code.
  POM cases are absent because a page object does not define browser tests.
- `LOGIN_REVIEW` stores the recorded review provenance for the existing login
  references. Reusing the note keeps both entries consistent; it does not transfer
  that approval to new scenarios or future edits to the login goldens.
- `CASES` is the explicit tuple of twelve `DatasetCase` objects. Each object names
  the file, scenario, kind, acceptance criteria, and allowed dependencies.

The `case_id` is the stable identifier used by the browser-count mapping.
The `scenario` will support grouping in LangSmith, and `kind` will separate POM
results from test-file results. `expected_behaviors` will be reference data for
review/evaluation; listing a criterion does not implement a check for it.

POM entries omit `companions`, using the dataclass's empty-tuple default. Test
entries explicitly supply their POM. In `("pages/AlertsPage.ts",)`, the trailing
comma makes a one-item tuple; parentheses alone would leave a plain string.

All 12 entries explicitly set `reference_review="reviewed"`. Login and alerts retain
their user-review notes; `COMPLETION_REVIEW` identifies agent curation for the
remaining eight under the user's instruction to finish 6.1. A manifest can describe
pending work, but `build_collection()` refuses to upload missing or pending cases.

## How this will shape the LangSmith reports

The snapshot builder already copies `case_id`, `scenario`, `kind`, review metadata,
and context policy into each example's metadata. The manifest now supplies
consistent values across the collection. The uploader adds measured fixture
evidence and coverage metadata. The scored converter runner remains future work.

Reports will state the number of conversion rows and browser cases separately,
show scenario and file-kind results, and identify supplied golden companions.
For example, a successful test-file conversion with an existing golden POM is
evidence about that task, not evidence that both files were generated correctly.

Keep the uncovered capabilities visible: editor typing, JavaScript prompt input,
confirmation acceptance, nested frames, and wider suite dependencies are outside
this initial matrix. The six selected scenarios are a curated development set;
they do not justify claims about every Selenium suite or a held-out benchmark.

## Local checks and next increment

The manifest audit confirmed 12 distinct case IDs and paths, six scenario pairs,
one matching POM companion per test file, and eight planned browser tests.
Both reviewed login pairs snapshot into the installed LangSmith SDK's example
format. At that checkpoint, ten pairs were pending and missing; snapshotting a
missing pair failed explicitly. These were local consistency checks with no model
or cloud calls. All 36 offline tests passed again after adding the alerts tests.
Subsequent alerts POM checks are recorded in alerts-page-objects.md;
the two Selenium/two Playwright browser passes and mutation check are in alerts-tests.md.

The user subsequently requested finishing 6.1. The four remaining scenarios are
implemented and browser-verified; all eight tests per framework pass. The complete
dataset was uploaded and verified through exact versioned readback. The user's
screenshot confirms 12 examples in the UI. Step 6.2 is next; the full completion
evidence and limits are in [phase-6.1-report.md](phase-6.1-report.md).
