# Phase 6.2 — first live deterministic evaluation

Phase 6.2 is complete: the real converter ran on the pinned 12-example dataset,
independent evaluators scored every result, and the experiment is visible in
LangSmith. **11/12 files passed all four static checks (91.67%); 9/12 received a
fully passed graph report (75%).** These are static conversion measurements,
not a browser behavior success rate.

[Open the LangSmith experiment](https://smith.langchain.com/o/32ac11b4-3e72-4765-a59b-5dc1bcd32cbe/datasets/33c80b1e-96bd-4b5b-a9c1-ca49d215828f/compare?selectedSessions=43959805-b945-41c8-a46b-2ec3142148b8).

The original upload lost some records. Recovery restored the authentic saved root
outputs and feedback without repeating conversions, evaluators, or changing scores.
API readback now verifies **12 roots and 96 feedback entries**. Two model calls have
incomplete cloud evidence, and the complete monetary cost remains unavailable.
Chrome displays the failed row's four zero scores, but its summary still shows
`1.00 AVG` and `92% evaluated` after refresh. That summary disagrees with the
verified row-level data; the correct denominator is all 12 scheduled examples.

## What was held fixed

| Setting | Recorded value |
| --- | --- |
| Experiment | `s2p-6.2-claude-opus-5-ba0e2bd3` |
| Experiment ID | `43959805-b945-41c8-a46b-2ec3142148b8` |
| Dataset | `selenium2playwright-v1-4920b5f319d8` |
| Dataset ID | `33c80b1e-96bd-4b5b-a9c1-ca49d215828f` |
| Pinned dataset version | `2026-09-06T03:05:09.476354+00:00` |
| Dataset collection SHA-256 | `4920b5f319d827a25a3d8f1a2f026c430e1fb20bdb897c6b9c3599f55b8aeb3d` |
| Conversion code revision | `b440966e73e68c9df37479180bac8980f3d6e8da`, clean worktree |
| Configuration SHA-256 | `0909323c71defaf78961836845e3f52bbd846d51e6778a83f972aec92b4d783f` |
| Requested/observed model | `anthropic:claude-opus-5` / Anthropic `claude-opus-5` |
| Repair budget | Three total actor attempts: initial + at most two repairs |
| Execution | One repetition per example; max concurrency 1 |
| Actor output | `ConversionResult` structured tool response; 8,192 output-token limit |
| Critic output | Native `json_schema` response; medium effort requested; 8,192 output-token limit |
| Evaluators | `deterministic-v1`: compile, residue, typed lint, parity |
| Start UTC | `2026-09-06T05:57:41.221731+00:00` |
| Execution finish UTC | `2026-09-06T06:01:07.400329+00:00` |

The installed versions include LangSmith 0.12.1, LangChain 1.3.18,
langchain-core 1.6.1, langchain-anthropic 1.7.0, LangGraph 1.2.11, and
Pydantic 2.13.5. The plan records the full dependency/tool identity and hashes the
graph, prompts, playbook, validators, runner, and dependency/configuration files.
Observed trace metadata shows the requested model, an 8,192-token limit, and SDK
`max_retries=2`; provider retries are distinct from graph repair attempts.
Temperature was not explicitly set. The actor's forced tool choice and critic's
JSON schema are visible in persisted model metadata. Request settings do not prove
the provider used every optional setting internally.

This benchmark converts isolated files. Six rows convert page objects; six convert
test files using the supplied golden page object as context. Reference answers for
the target file stay outside the model's input. Converted POMs are not fed into the
test rows, so passing both rows does not establish cross-file compatibility.
Temporary absolute source paths vary between target invocations.

## Scores with the full denominator

| Metric | Passed / scheduled | Percent | Other outcomes |
| --- | --- | --- | --- |
| `compiles` | 11 / 12 | 91.67% | 1 `no_output` |
| `residue_free` | 11 / 12 | 91.67% | 1 `no_output` |
| `typed_lint_pass` | 11 / 12 | 91.67% | 1 `no_output` |
| `parity_pass` | 11 / 12 | 91.67% | 1 `no_output` |
| All four static checks | 11 / 12 | 91.67% | Same missing-output row |
| Graph report `passed` | 9 / 12 | 75.00% | 2 converted with review TODOs; 1 conversion failure |

`no_output` earns zero because there is no artifact to establish a pass. It does
not mean four tools independently found defects in WindowsPage: they did not run
against absent code. Each score has a matching status entry and error evidence.
On all 11 returned candidates, all four independent checks passed. That conditional
11/11 observation does not replace the primary 11/12 benchmark rate.

| Group | All static | Graph passed |
| --- | --- | --- |
| Page objects | 5 / 6 | 3 / 6 |
| Test files | 6 / 6 | 6 / 6 |
| Alerts | 2 / 2 | 1 / 2 |
| Dynamic loading | 2 / 2 | 2 / 2 |
| Iframe | 2 / 2 | 2 / 2 |
| Login | 2 / 2 | 1 / 2 |
| Upload | 2 / 2 | 2 / 2 |
| Windows | 1 / 2 | 1 / 2 |

## Every conversion, including failures and review work

| Case | Actor attempts | Static result | Graph report | Review TODOs | Target seconds | Usable root cost USD |
| --- | --- | --- | --- | --- | --- | --- |
| alerts-page | 2 | All passed | needs-review | 2 | 44.462 | 0.153063 |
| alerts-test | 1 | All passed | passed | 0 | 9.423 | 0.035753 |
| dynamic-loading-page | 1 | All passed | passed | 0 | 12.935 | 0.030543 |
| dynamic-loading-test | 1 | All passed | passed | 0 | 9.867 | 0.027908 |
| iframe-page | 1 | All passed | passed | 0 | 16.427 | 0.040268 |
| iframe-test | 1 | All passed | passed | 0 | 10.611 | 0.029998 |
| login-page | 2 | All passed | needs-review | 2 | 35.412 | 0.097531 |
| login-test | 1 | All passed | passed | 0 | 9.197 | 0.030798 |
| upload-page | 1 | All passed | passed | 0 | 14.185 | 0.035618 |
| upload-test | 1 | All passed | passed | 0 | 10.821 | 0.037293 |
| windows-page | 1 | no_output | needs-review; conversion failed | unavailable | 11.142 | unavailable |
| windows-test | 1 | All passed | passed | 0 | 14.656 | unavailable |

### WindowsPage: structured output failed before a usable draft existed

The model returned `notes` as a string, but `ConversionResult.notes` requires a
list of strings. Pydantic rejected that response. The graph retained the parse
error, returned no usable code, and assembled a needs-review failure report after
one attempt. No critic ran for this row. Its four independent evaluators returned
`score=0`, `status=no_output`, and `MissingCode` evidence.

This is a converter reliability finding. We did not coerce the response after the
fact, edit the result, or retry until it passed. Schema-repair policy can be a later
implementation change measured against this preserved baseline. The Windows test
row passes because it receives the golden WindowsPage as input context.

### AlertsPage: critic requested review evidence and a safer selector mapping

The first critic verdict was `revise`. It found that accessible button names had
been inferred from CSS `onclick` selectors without source markup proving them.
It also requested explicit review tracking for a behavioral difference: registering
a one-shot dialog handler does not itself assert that a dialog actually appeared.

The second actor returned the faithful CSS selectors and two TODOs, one for each
dialog method. It retained `dialog.dismiss()` for Cancel. The second critic passed,
and all static checks passed, but the final graph report correctly stayed
`needs-review` because the two TODOs remained. A critic pass does not erase review
work. The model's wording about dialog equivalence is its review note, not a claim
that Playwright cannot wait for a dialog event.

### LoginPage: critic tracked assumptions and a public API change

The first critic also requested `revise`. It flagged unverified `Username`,
`Password`, and `Login` accessible names inferred from IDs/CSS, plus the removal of
`getFlashText()` in favor of a public `flashMessage` locator. The second actor kept
the semantic locators and added both an in-code TODO and ledger entry for those
assumptions, plus a TODO for updating callers. The second critic passed; the graph
still required review. Neither repair used the third allowed actor attempt.

## Time, usage, cost, and child-trace coverage

Execution took **206.177 seconds**, including the additional evaluator checks and
local orchestration, before final cloud readback. Summed target time was **199.137
seconds**. Recovery and subsequent inspection time are outside those execution
measurements. Time varies with network/provider/tool conditions; this one run is
not a latency distribution or a model comparison.

The graph recorded **63,344 actor tokens** across all 12 rows and **62,095 critic
tokens** across the 11 rows with critic usage. The known combined usage is
**125,439 tokens**. The WindowsPage critic field stays null in the raw report;
its parse failure prevented a critic call. We preserve that field rather than
silently replacing missing usage with zero. Detailed input/output/cache/reasoning
token categories remain in each row's saved output and LangSmith root output.

There were 14 actor attempts and 13 critic calls, hence 27 expected model calls.
The child audit found **26 model traces: 25 completed and one unfinished**. The
WindowsPage actor's completion is missing; the Windows test actor trace is absent.
Its critic trace is present. No synthetic child traces were created during recovery.
These gaps prevent a claim that the complete prompt/response history persisted.

The audit captured 433 total trace nodes after restoring the missing root. Model
identity is established from the persisted calls' actual metadata and completed
responses. The missing calls' requested identity is available in the pinned plan,
but their full provider responses cannot be independently inspected in LangSmith.

Cloud costs for ten roots have token totals matching the original actor + critic
usage. Their known subtotal is **$0.518773**. Both Windows row costs are unavailable
for complete-row accounting. The complete experiment cost is therefore
**unavailable**, not $0.518773 and not the UI's displayed partial aggregate.
LangSmith cost is provider-price accounting, not an independently checked invoice.

## The upload incident and recovery evidence

The SDK rejected background multipart batches with a feedback-definition conflict:
the payload declared continuous bounds 0–1 while the stored definition had no
bounds. Initially 11 roots and 72 feedback records were readable; WindowsPage was
unfinished and the Windows test root was missing. Upload-test, WindowsPage, and
Windows-test were each missing eight feedback records.

Recovery restored one root, finished one empty root, and created the 24 missing
feedback records from the unchanged local journal. Readback compared root inputs,
outputs, errors, completion, reference example IDs, project metadata, and all
scores/statuses/comments/evaluator evidence. It now verifies 12 roots and 96 feedback
entries. A repeated recovery preview planned zero writes.

The journal SHA-256 is
`e77031fb347cbd3d19b286186070d55d41a73d217b7bfd584dc7e68a1a9ae2b7`.
Before-recovery reports/readbacks are retained alongside each recovery receipt.
The original model/evaluator configuration remains revision `b440966`; subsequent
code only fixes feedback transport, recovery, inspection, and accounting/reporting.
The converter, prompts, playbook, golden answers, and original scores were unchanged.
The original live command exited 2 for unverified evidence. Readback-only now exits
1: evidence is verified, but the baseline has quality failures. Exit 1 is expected.

See [the recovery lesson](evaluation-recovery.md) for theory, function explanations,
repeatability guarantees, code comments, tests, and the relevant commands.

## Reading this experiment in LangSmith

1. Open the experiment link above and inspect its 12 rows. The WindowsPage row
   displays `failed`, four `0.00` scores, and four `no_output` statuses.
2. Open a numeric feedback entry. Its comment describes the verdict; its
   feedback-source metadata retains evaluator version, gate, status, elapsed time,
   complete validation report/raw tool output, and typed error evidence.
3. Open a root output. Inspect `code`, `conversion_status`, `report.status`,
   `report.attempts`, TODOs, notes, critic verdict, graph errors, refusal,
   `adapter_error`, actor/critic usage, and elapsed time. These fields answer
   different questions; a successful upload is not a successful conversion.
4. For AlertsPage and LoginPage, inspect both actor/critic rounds. The first critic's
   explicit `revise` fixes explain the second draft. Read the final TODO ledger
   alongside the final critic pass to understand `needs-review`.
5. Treat the Windows child-trace gaps and the incorrect summary average as known
   evidence limitations. The corrected 11/12 scorecard is in this experiment's
   description and this checked-in report. Do not quote the displayed 1.00 as the
   benchmark result.

Raw local artifacts are under
`out/6.2/experiment-20260906T055741Z-f4465fd0/`: `plan.json`, `experiment.json`,
`results.jsonl`, `report.json`, `report.md`, `cloud-readback.json`, `trace-audit.json`,
and recovery plans/receipts. Chrome's accessibility data established the visible
rows and summary discrepancy; a screenshot was not retained because the active tab
changed during capture. These generated artifacts remain
ignored. [The tracked receipt](phase-6.2-receipt.json) preserves identities, hashes,
scores, usage, recovery counts, and trace coverage without relying on a local folder
being available to every reader.

## Verification and boundaries

The full offline suite passed **86 tests in 57.434 seconds**. It includes the real
SDK orchestration test, independent validators, malformed inputs, missing output,
partial execution, feedback/schema integrity, conservative cost accounting, and
recovery conflict/duplicate checks. Log: `out/6.2/completion-offline-tests.txt`.
Live readback verified every scheduled root and all required feedback. Chrome
inspection verified row visibility and documented the summary discrepancy.

No generated candidate was browser-tested in this increment. Phase 6.1's eight
Selenium and eight golden Playwright passes validate the fixtures, not these model
outputs. The Cancel→OK negative control already showed that wrong behavior can
pass all four static gates. Iframe typing remains uncovered. The twelve rows are
a small, related benchmark; their percentages are descriptive, not general
production reliability estimates. Successful isolated test rows also do not prove
the generated POM and generated test form a compatible suite.

## Next learning step

Phase **6.3** compares one actor attempt with up to three while keeping dataset,
model, prompts, and evaluator policy fixed. First make the attempt cap configurable
and teach how a controlled comparison differs from observing two successful repairs
in this run. This baseline alone cannot establish reflection's effect on quality,
latency, or cost. Phase 6.3 has not started.
