# Step 5.1 — the critic node

The conversion call writes code. A second model call reviews it against the
original source and the deterministic findings:

```text
intake → convert → validate → critic → END
       ↘ refuse → END
```

Implementation, offline checks, and the learning review are complete.
The critic reports fixes. Step 5.2 will feed those fixes into another conversion.

## The result shape: schemas.py

[`Critique`](../src/selenium2playwright/schemas.py) has two fields:

```python
Critique(verdict="pass", fixes=[])
Critique(verdict="revise", fixes=["LoginPage.ts:23: await the fill() call."])
```

The verdict is restricted to `pass` or `revise`. A Pydantic model validator rejects
a pass with fixes, a revision without fixes, and blank fix instructions. This
makes inconsistent model replies explicit parse failures.

## The review prompt: prompts.py

[`build_critic_prompt`](../src/selenium2playwright/prompts.py) combines a static
review rubric and the playbook, followed by the evidence for this conversion:
source code, the converted result including notes/TODOs, companion contents, and
all four reports. Reports have explicit PASS/FAIL labels, finding locations, and
raw diagnostics when a failed gate has no structured findings.

The rubric asks for actionable repairs, preserved behavior and assertions,
web-first assertions, correct asynchronous calls, and honest TODOs. It forbids
inventing locator labels or runtime results. Tool failures require restoring
validation, not weakening the code to hide the failure. Correctly recorded TODOs
and optional style warnings do not automatically demand another rewrite.

## The node: graph.py

[`critic`](../src/selenium2playwright/graph.py) follows the converter's familiar
chain: prompt → provider message preparation → structured model. It uses
`with_structured_output(Critique, method="json_schema", include_raw=True)`.

The native JSON method constrains the reply format; Pydantic checks the parsed
object. `include_raw` also exposes token usage and parsing errors. The official
[LangChain structured-output documentation](https://docs.langchain.com/oss/python/langchain/models#structured-output)
explains the schema-based interface. The selected model/provider must support
native JSON schema output. The installed Anthropic adapter supports it.

[`make_model`](../src/selenium2playwright/llm.py) accepts `for_critic=True`.
For Anthropic, this explicitly selects medium reasoning effort. Provider-specific
configuration stays in that file; conversion retains its existing settings.
Native JSON avoids forcing a tool call while using adaptive thinking.

The node returns `critique`, `critic_usage`, and `critique_error`, leaving the
converted result and validation reports in state. Two failure cases matter:

- If the model says pass while any validator failed, code changes the verdict
  to revise and derives fallback instructions directly from the failed reports.
  Those instructions are deterministic evidence, not a claimed model discovery.
- If the model call or reply parsing fails, `critique` is `None` and
  `critique_error` records why. The CLI still emits the code and validation
  scorecard, prints `Critic: UNAVAILABLE`, and returns exit 1.

`build_graph` connects `validate → critic → END`. Refused inputs bypass both
model calls. Each supported conversion has one conversion and one review node;
there is no conversion/critique loop in this step.

## Display and verification

`report_critique` prints PASS or REVISE and each fix to stderr, after the validation
scorecard. Critic token usage is labeled separately. Generated TypeScript still
goes to stdout or `--out`. Exit 0 now requires both the gates and the critic to
pass; exit 1 includes a revision request or unavailable review. Exit 2 continues
to mean refusal or invalid CLI arguments.

```sh
uv run python -m selenium2playwright.graph samples/selenium-suite/pages/LoginPage.ts
uv run python -m unittest discover -s tests -v
```

The 29 offline tests cover the previous validators, evidence passed to the critic,
schema contradictions, failed-gate overrides, unavailable reviews, preserved code
and exit codes, and one review per invocation. An additional SDK-level test uses
the real Anthropic client with a mock HTTP transport to verify native JSON,
reasoning effort, response parsing, and usage collection without network calls.

These tests establish the wiring and response contract. They do not measure
whether a live model identifies semantic mistakes reliably; that requires the
later evaluation dataset. A critic pass remains a review opinion, not proof of
runtime correctness.
