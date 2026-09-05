# Step 4.5 — the validation node

Every supported conversion now flows through all four deterministic gates:

```text
intake → convert → validate → END
       ↘ refuse → END
```

Implementation, offline checks, and the learning review are complete.
This walkthrough records step 4.5. The current graph also runs the
[step 5.1 critic](critic-node.md) after validation; 5.2 will add the repair loop.

## What changed, and why

[`ConversionState`](../src/selenium2playwright/graph.py) gains
`validation: list[ValidationReport]`. The list keeps each gate's verdict, findings,
and raw output together, in execution order. `validate` returns only
`{"validation": reports}`; LangGraph merges that update into the existing state.
The conversion result survives regardless of the validation verdict. This is the
partial state update pattern described in the official
[LangGraph state documentation](https://docs.langchain.com/oss/python/langgraph/graph-api#state).

The node has three parts:

1. Prepare the output file and supplied companions in their intended relative
   layout. The compiler can then resolve imports such as `../pages/LoginPage`.
   Only explicitly supplied companions are copied into validation.
2. Run compile, residue, lint, and parity in order. Compile/residue/lint inspect
   output plus companions. Parity compares the current source/output pair;
   there is no original Selenium counterpart for an already-converted companion.
3. Collect every report. A failed gate does not skip later gates. A missing tool,
   timeout, or other expected tool failure becomes `validator-error` on its layer.

Intake now captures companion contents in `context_files`. The prompt formatter
accepts that snapshot, and validation uses it too. Editing a companion on disk
during the model call cannot change the code being checked. Prompt wording and
the one-shot converter's existing formatter calls retain their behavior.

`build_graph` adds `validate` and replaces `convert → END` with
`convert → validate → END`. Refused inputs bypass both conversion and validation.

## Run a conversion

With Python dependencies and the sandbox Node dependencies installed:

```sh
uv run python -m selenium2playwright.graph samples/selenium-suite/pages/LoginPage.ts \
  --out out/4.5/pages/LoginPage.ts

uv run python -m selenium2playwright.graph samples/selenium-suite/tests/login.spec.ts \
  out/4.5/pages/LoginPage.ts --out out/4.5/tests/login.spec.ts
```

The source is first, followed by zero or more already-converted companion files.
`--out` determines where the converted file belongs and writes the result there.
The second command's output and companion share `out/4.5/`, preserving the test's
relative import. Without `--out`, the input file's location anchors validation
and converted code is printed to stdout.

The scorecard, findings, notes, and token usage go to stderr. A clean result shows:

```text
Validation (report-only):
  PASS compile: 0 finding(s)
  PASS residue: 0 finding(s)
  PASS lint: 0 finding(s)
  PASS parity: 0 finding(s)
```

Since step 5.1, exit 0 requires every gate and the critic to pass. Exit 1 also
covers a critic revision request or unavailable review. Exit 2 means the input
was refused or CLI arguments were invalid. Lint warnings are displayed
without failing the gate. Code is still emitted on validation failure so it can
be inspected. The CLI rejects `--out` paths that would replace the source or a
supplied companion. Unprovided imports remain real compile findings.

## Verification and limits

```sh
uv run python -m unittest discover -s tests -v
```

At step 4.5, all 20 tests passed: 12 parity regressions and 8 graph integration tests. Graph
tests use fixed model replies through the real conversion chain and run the real
validators, except where a tool error or lint warning is deliberately injected.
They cover POM and test conversions, relative companion imports, unchanged
snapshots after disk edits, missing imports, dropped assertions/awaits, scorecard
streams and exit codes, tool failures, and refusal bypasses.

No live model call or new LangSmith trace was used to verify this increment.
Passing these static gates does not prove runtime or semantic equivalence;
the critic adds review, and execution evaluations remain a later step.
