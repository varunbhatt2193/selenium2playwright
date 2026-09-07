# Step 7.2 — human-in-the-loop: `interrupt()`

*What this step adds, in one sentence: when a file contains a pattern that has
**more than one correct** Playwright translation, the agent stops mid-run and
asks you — and your answer, not its guess, is what reaches the model.*

---

## 1. The problem, concretely

The whole project rests on one rule: *the model can't lie to the compiler*. Four
deterministic gates plus a critic catch anything that is **wrong**.

They cannot catch anything that is merely **not what you meant**. Consider
`samples/selenium-suite/pages/AlertsPage.ts`:

```ts
async dismissConfirm(): Promise<void> {
  await this.driver.findElement(this.confirmButton).click();
  const dialog = await this.driver.wait(until.alertIsPresent(), 5000);
  await dialog.dismiss();          // Cancel is the behaviour under test
}
```

Three different Playwright versions of that method compile, lint, keep parity,
and satisfy the critic:

1. register `page.once("dialog", d => d.dismiss())` **before** the click;
2. `Promise.all([page.waitForEvent("dialog"), click()])`, then dismiss;
3. register nothing at all — Playwright auto-dismisses unhandled dialogs.

Option 3 even produces the same result *for this method*, and silently the wrong
result for `acceptAlert()` next door, because auto-dismiss is Cancel. No gate
can tell you which one your team wanted. Grep the playbook for "dialog" and you
get nothing: this was never settled, and a model asked to settle it will just
pick something confident.

Same story for two other patterns:

* **`executeScript`** — usually a workaround for something WebDriver could not
  do (scroll into view, click through an overlay). Playwright often does it
  natively. But sometimes the script *is* the thing under test, and replacing it
  quietly changes what the test proves.
* **a `before` hook that logs in once** — works in Selenium because every test
  drives the same browser. Playwright gives each test a fresh context, so that
  shared session has to be rebuilt: per-test login, a saved `storageState`, or
  an explicitly serial suite. Those are three different suites.

The honest move is not a better guess. It is a question.

## 2. The one word: `interrupt()`

```python
from langgraph.types import Command, interrupt

answer = interrupt({"question": "How should dialogs be handled?"})   # inside a node
```

`interrupt()` **suspends the whole graph** from inside a node. Concretely:

* everything written so far is already in the checkpointer, so the run is safe
  on disk;
* `invoke()` returns immediately, carrying `__interrupt__` — the payload you
  passed — instead of a finished report;
* the run continues only when someone invokes **the same thread** with
  `Command(resume=answer)`;
* on resume the node is **re-run from its first line**, and that `interrupt()`
  call returns the answer instead of raising.

```
invoke({source_path: ...}, thread="alerts")   → {..., "__interrupt__": [question]}
                                                    ↓ a human reads it
invoke(Command(resume="handler-first"), thread="alerts")   → the finished report
```

Two consequences fall straight out of that description, and both shaped the code:

**It needs a checkpointer.** There is nowhere to suspend *to* without one. This
is why 7.2 could not have come before [7.1](short-term-memory.md), and why the
CLI only offers to ask on a `--thread` run.

**The node must be safe to run twice.** LangGraph replays it from the top on
every resume. A node that called the model before its `interrupt()` would pay
for that call once per question. So the asking node does nothing but ask.

Docs: <https://docs.langchain.com/oss/python/langgraph/interrupts>

## 3. What we built

### `risk.py` — the detector, ~230 lines, no LLM and no network

Regex over the source, same policy as `classify.py`. Deterministic on purpose:
the node that asks is replayed on every resume, so the same file must raise the
same questions in the same order, every time.

| piece | job |
|---|---|
| `Risk(kind, line, snippet, count)` | one flagged pattern — small, because it goes into the checkpoint |
| `RISKS` | the catalogue: title, why-it-needs-you, question, and answer options per kind |
| `detect_risks(source)` | at most **one Risk per kind** — the answer is a policy for the whole file |
| `question(risk)` | the plain dict handed to `interrupt()`: everything a front end needs to render it |
| `resolve(kind, answer)` | an option key → that option's guidance sentence; empty → the default; anything else → the user's own words, verbatim |
| `decision_lines(...)` | the answered risks, rendered for the prompt |

The answer options are written as **instructions to the model**, not labels. For
example `dialogs` / `auto-dismiss` reads, in full:

> Do not register a dialog handler: rely on Playwright's default auto-dismiss and
> assert only the resulting page state. Apply this ONLY where the Selenium code
> dismissed the dialog; where it accepted one, keep an explicit handler and add a
> TODO(review) saying auto-dismiss would have changed the branch under test.

That second sentence is the honesty contract surviving contact with a user
instruction: you can choose the policy, you cannot choose the truth.

### The `risk_review` node

```
START → intake → risk_review → convert → validate → critic ⇄ convert
              ↘ refuse                                    ↘ assemble → END
```

`intake` detects the risks (it already reads the source and classifies it).
`risk_review` asks about the ones with no answer yet — one `interrupt()` each,
in a fixed order — and returns `{"decisions": {kind: answer}}`.

Three properties worth naming:

* **It is off by default.** `ask_risks` is a state input, false unless the CLI
  turns it on. Every Phase 0–6 run and the entire eval suite go straight through:
  risks are still detected and reported, nothing pauses, nothing is added to the
  prompt. The prompt stays byte-identical, so the eval baselines still hold.
* **An answer is per-conversation, not per-run.** `decisions` lives in the
  thread's state next to `conventions`, so a question answered on turn 1 is never
  asked again on turn 2 — and it is carried into every later model call.
* **The critic sees the answers too.** Otherwise it would report the branch you
  deliberately chose as a defect, and the repair lap would quietly undo you.

### The CLI

```bash
# pauses and asks, because --thread gives it somewhere to pause
uv run s2p convert --thread alerts samples/selenium-suite/pages/AlertsPage.ts

Paused — browser dialog handling
  found: line 21: const dialog = await this.driver.wait(until.alertIsPresent(), 5000);  (+3 more)
  why you: Selenium handles a dialog after the click; Playwright must register the handler
    before it, and auto-dismisses anything unhandled. Accept and dismiss run different
    application branches, so this cannot be inferred from the code.
  How should the converted code handle browser dialogs?
    1) handler-first — register page.once('dialog', ...) before the action [default]
    2) expect-event — await the dialog event alongside the action
    3) auto-dismiss — let Playwright auto-dismiss; assert only the page outcome
  answer (number, key, your own words, or Enter for the default):
```

* `--answer dialogs=auto-dismiss` answers up front, so scripts and CI never hang.
* `--no-ask` reports the risk and converts with the playbook default.
* No terminal (a pipe, a cron job) is never a silent guess: it prints
  *"no terminal to ask on; using the default (handler-first)"* and says which
  flag would have chosen otherwise.
* A run with no `--thread` still lists what it found, and how to be asked.

## 4. The sharp edges

**A paused `invoke()` does not return a report.** It returns whatever that
invocation managed to write, *plus* `__interrupt__`. Reading `final["report"]`
there gets you the previous turn's report, or a `KeyError` — the run has not
finished. The CLI loops on `"__interrupt__" in final` for exactly this reason.

**Resume values are matched to `interrupt()` calls by position.** With two
questions in one node, the first `Command(resume=...)` answers the first
`interrupt()`, and the node then re-runs and raises the second. That only stays
correct because the questions are generated deterministically in a fixed order —
which is the real reason `detect_risks` is a pure function over the source.

**`state.next` is not a reliable "am I paused?" signal.** After the *first*
interrupt it is `("risk_review",)`; after a resume that hits a *second*
interrupt it came back `()` while the task was still pending with interrupts on
it. Ask the reply for `__interrupt__`, or read `state.tasks[…].interrupts` —
`tests/test_risk.py` pins both.

**A new type in the state is a new line in `memory.CHECKPOINT_TYPES`.** `Risk`
had to be added there. The 7.1 failure mode is unchanged and still silent: an
unlisted class comes back as a plain dict, and the `AttributeError` arrives much
later. The round-trip test in `tests/test_memory.py` now covers `Risk` too.

## 5. Live proof

`scripts/demo_hitl.py` converts the same file twice — same model, same prompt,
same gates — and answers the same question differently. It plays the human:
runs until the graph pauses, prints the question, resumes that thread with
`Command(resume=<answer>)`.

```bash
caffeinate -i -s uv run python scripts/demo_hitl.py     # Sonnet actor + Sonnet critic
```

Both arms paused before spending a single token. Both then **passed all four
gates and the critic with no open TODOs** — the answer changed the design, not
the quality. 30 lines differ:

```diff
   async acceptAlert(): Promise<void> {
-    // Register the dialog handler before triggering the action that opens it.
-    this.page.once("dialog", (dialog: Dialog) => {
-      this.lastDialogMessage = dialog.message();
-      void dialog.accept();
-    });
-    await this.alertButton.click();
+    const [dialog] = await Promise.all([
+      this.page.waitForEvent("dialog"),
+      this.alertButton.click(),
+    ]);
+    await dialog.accept();
   }
```

Two details worth more than the diff:

**The model cited the human.** Note 3 of the `handler-first` run reads:
*"Human decision 1: dialog handling converted to `page.once("dialog", ...)`
registered immediately before the click that triggers it, calling
accept()/dismiss() to match the original Selenium behavior exactly."*

**The choice had a consequence, and the loop cleaned it up.** `handler-first`
needed two attempts: a synchronous `once` callback cannot await, so attempt 1
left `dialog.accept()` floating and the **lint gate failed** on
`no-floating-promises`. Attempt 2 marked them `void` and added a
`lastDialogMessage` field so the test file can still assert the dialog's text
(the critic asked for that — a page object must not assert). `expect-event`
needed one attempt and 4.7k tokens against 11.5k, because awaiting the event is
the shape the linter likes. That is a genuine engineering trade-off between two
correct answers, and it is now yours to make rather than the model's.

| arm | pauses | attempts | gates | critic | actor tokens |
|---|---|---|---|---|---|
| `handler-first` | 1 | 2 | 4/4 | pass | 11,523 |
| `expect-event` | 1 | 1 | 4/4 | pass | 4,702 |

Artifacts (gitignored): `out/7.2/{handler-first,expect-event}.ts`,
`answers.diff`, the two reports, and `demo-receipt.json` with the four LangSmith
trace URLs — two per arm, because a resumed run is a second trace.

## 6. What this step is *not*

* **Not a safety review.** The detector finds ambiguity, not danger. A file with
  no flagged pattern is not certified; it just has no open question.
* **Not a general approval gate.** It never asks "is this conversion OK?" — the
  gates and the critic answer that. It asks only what they cannot.
* **Not cross-file.** Answers live on the thread. A second file, in a second
  thread, asks again. Carrying a decision across conversations is the next step
  (7.3, long-term memory in the Store).
* **Not asked by default.** No `--thread`, no questions. That is deliberate: the
  eval suite must keep measuring the same agent it measured in Phase 6.

## 7. Review checklist

1. Why does `intake` detect the risks and `risk_review` only ask about them?
2. What breaks if `detect_risks` returned its risks in a different order on a
   resume than it did on the first run?
3. Why must an answer be given to the critic as well as the actor?
4. Why does a run with no `--thread` still detect risks it will never ask about?
5. The `auto-dismiss` option's text tells the model to *refuse* to apply it in
   half the file. Why is that in the option and not in the playbook?
