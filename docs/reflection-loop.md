# Step 5.2 — bounded repair and final assembly

The critic's fixes now feed another conversion. Every new draft goes through all
four validators and the critic again. A successful review proceeds to assembly;
unresolved failures stop after **three conversion attempts total**: the initial
draft plus at most two repairs. Implementation and checks are complete; the user
approved committing and pushing this step on 2026-09-05.

## Where the code lives

| File | Responsibility |
|---|---|
| [graph.py](../src/selenium2playwright/graph.py) | Attempt counter, conditional routes, final assembly, CLI output |
| [reflection.py](../src/selenium2playwright/reflection.py) | Repair feedback, token totals, TODO collection, three-attempt constant |
| [prompts.py](../src/selenium2playwright/prompts.py) | Append repair evidence as a literal message |
| [schemas.py](../src/selenium2playwright/schemas.py) | Final `ConversionReport` contract |
| [test_reflection.py](../tests/test_reflection.py) | Repair, stopping, failure preservation, and TODO regressions |
| [demo_reflection.py](../scripts/demo_reflection.py) | Reproducible seeded demo using the live model and LangSmith |

## Read the changes in this order

1. **Intake resets the run.** `iteration` starts at zero. Old results, reports,
   errors, and usage are cleared so a fresh invocation starts fresh. Source and
   companion contents are captured once and stay fixed throughout the repairs.
2. **Convert counts attempts.** It increments `iteration` before calling the
   model. On repairs, `revision_feedback` supplies the previous complete result,
   all validation evidence, and the critic's fixes, alongside the original source.
   The model must return a complete replacement file. The literal message keeps
   TypeScript braces from being interpreted as prompt-template placeholders.
3. **Validate and review again.** A draft's old scorecard never approves its
   replacement. Each new draft receives fresh compile, residue, lint, and parity
   reports, then a fresh critic review.
4. **Choose a route.** `route_after_critic` returns `convert` for a revision
   request only if fewer than three attempts have run and the critic/validation
   tools are available. Otherwise it returns `assemble`. This follows LangGraph's
   [conditional-edge pattern](https://docs.langchain.com/oss/python/langgraph/graph-api#conditional-edges).
5. **Assemble the result.** `ConversionReport` includes status, attempt count,
   stop reason, latest available code/notes/TODOs, final scorecard, critic, and
   errors. This report is available to direct graph callers as `final["report"]`.

The CLI's recursion limit is a second safeguard with enough room for all three
laps and assembly. The explicit counter controls normal stopping; the graph
does not rely on a recursion exception to end a run.

## When the run needs review

- Findings still exist after the third attempt.
- A critic or validator is unavailable. Rewriting code cannot restore a tool.
- A conversion call or response parsing fails. Any previous draft and its
  scorecard remain available. If the first conversion fails, no code is invented
  and `--out` does not replace an existing file with empty output.
- The gates and critic pass, but the final code or its ledger contains open
  `TODO(review)` items. These need human input, so they do not trigger more rewrites.

Assembly merges the model's final TODO ledger with a conservative scan of markers
in the final code. Markers in strings may also be flagged; this is not an AST
comment parser. Only the latest draft is scanned, so resolved TODOs from older
drafts disappear. Token totals include every attempt and nested cache counts.

The report goes to stderr; the latest code goes to stdout or `--out`. Exit 0
requires all gates and the critic to pass with no open TODOs. Exit 1 means
`needs-review`; exit 2 remains refusal or invalid CLI arguments.

## Verification

All 36 offline tests pass. New cases cover a missing-await repair, unchanged
failures stopping at attempt 3, semantic review requesting repairs even with green
gates, success on the last allowed attempt, unavailable tools/models, preservation
of an earlier draft or existing output file, resolved and open TODOs, and token
totals. The repair test checks the streamed node sequence and the next model's
actual prompt, while using the real validators.

```sh
uv run python -m unittest discover -s tests -v
```

## Live demo

```sh
uv run python scripts/demo_reflection.py
```

The demo deliberately injects a first draft made from the golden POM with one
`await` removed. The trace labels this seed. All critic calls and later conversion
calls use the configured live model; this is not an unseeded quality evaluation.

Observed on 2026-09-05: attempt 1 compiled but failed lint. The live critic named
the missing await. Attempt 2 fixed it and passed all four gates and the critic.
Two locator TODOs remained because the Selenium source did not establish the
page's labels/button name. Assembly correctly returned `needs-review`, and the
demo exited 1. That is an honest completed run, not an execution failure.

The demo writes `out/5.2/LoginPage.ts`, `report.json`, `trace.json`, and, when URL
lookup succeeds, `trace-url.txt`. These artifacts are gitignored. The first live
run ID was `64ce108a-d3e7-46e6-87b2-cc660f2f50cf`; inspect it in the configured
LangSmith project. A trace demonstrates the repair mechanism; semantic quality
across inputs still requires Phase 6's evaluation dataset.
