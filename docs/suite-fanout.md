# Step 9.2 — fan-out: one graph, many files, at the same time

> **In one line:** `s2p suite` takes 9.1's wave plan and runs it — every file in
> a wave converted **in parallel** by the same graph `s2p convert` uses, all
> inside one LangSmith trace, with the results gathered by a **reducer**.

Step [9.1](suite-scan.md) read the folder and produced a plan. This step runs
it. Three LangGraph ideas do all the work, and this document is mostly about
those three: **`Send`**, **reducers**, and **subgraphs**.

---

## 1. The problem with the obvious answer

The obvious way to convert twelve files is a `for` loop around the single-file
graph. It works. It is also wrong in three separate ways:

* **Nothing overlaps.** Twelve files means twelve model round trips end to end,
  one after another, even though most of them have nothing to do with each other.
* **The trace falls apart.** Twelve unrelated runs in LangSmith, with nothing
  saying they were one job. You cannot ask "how long did the suite take" or
  "which file was slowest" of twelve separate traces.
* **The order is an accident of the loop.** The suite's real order is in its
  import graph, and a loop does not know about it.

LangGraph's answer to all three is to make the fan-out part of the graph.

## 2. `Send` — a branch per file, decided at run time

A conditional edge normally returns the **name** of the next node:

```python
def route_after_intake(state) -> Literal["recall", "refuse"]:
    return "recall" if state["classification"].supported else "refuse"
```

It may instead return a **list of `Send(node, payload)` objects**:

```python
def dispatch(state) -> list[Send] | str:
    ...
    return [Send("convert_file", FileJob(path=path, ...)) for path in waves[number - 1]]
```

LangGraph then starts **one copy of `convert_file` per `Send`**, each with its
own payload, all in the same super-step. Two things about that are worth
sitting with:

* **The number of branches is data, not wiring.** Six files means six copies;
  a suite of two hundred means two hundred. Nothing in `build_suite_graph()`
  had to know.
* **The payload *is* the branch's input state.** A `Send`'s argument is not
  merged into the parent state — the node is started with exactly that dict.
  That is why `convert_file(job)` reads as a function of one file's job, and
  why the node is declared with its own schema:

  ```python
  builder.add_node("convert_file", convert_file, input_schema=FileJob)
  ```

In sync mode LangGraph runs those copies on a thread pool, so six files waiting
on six model replies wait **once**. `--parallel N` is the cap
(`config={"max_concurrency": N}`) — without one, a forty-file wave would open
forty connections to the provider at the same instant.

## 3. The reducer — the join half

Every branch finishes by writing to the same state key. LangGraph's default
rule is *one value per key per super-step*, so two branches writing `outcomes`
is an **error**, not a merge:

```
InvalidUpdateError: At key 'outcomes': Can receive only one value per step.
```

A **reducer** is the function that says how to combine them:

```python
class SuiteState(TypedDict, total=False):
    outcomes: Annotated[list[FileOutcome], operator.add]
```

`operator.add` on two lists is concatenation, so each branch returns a one-item
list (`{"outcomes": [outcome]}`) and LangGraph appends them all. This is not
decoration: delete the annotation and the graph stops running at the first wave
with two files in it. `tests/test_suite_graph.py` proves that rather than
asserting it — it builds the same tiny fan-out twice, once with the reducer and
once without, and shows the error.

Two consequences fall out of it:

* **Results arrive in completion order, not plan order.** The fastest file is
  first in the list. `suite_graph.ordered()` re-sorts by `(wave, path)` at the
  point of reading.
* **A reduced channel can only be appended to, never rewritten.** Returning a
  sorted list from a later node would *append the sorted copy*. That is why the
  sort lives where the value is read and not in a node.

## 4. The subgraph — the per-file work is not new code

`convert_file` calls the Phase 0–8 graph:

```python
child = single.build_graph(store=store)
final = child.invoke(inputs, config={"run_name": f"convert:{job['path']}", ...},
                     context=child_run)
```

Everything that means is unchanged: classify or refuse, draft, four gates,
critic, up to three repair laps. Calling it rather than inlining it keeps one
definition of "convert a file" — a fix to the loop fixes it for both surfaces —
and LangSmith nests the child run under this one, so a suite trace reads
`suite-graph → convert:pages/LoginPage.ts → attempt → model call`.

The suite's context is handed straight down: `--model opus` on a suite is opus
for all twelve files, because `SuiteSettings` is copied into the child's
`RunSettings` at every branch.

## 5. The shape

```
START → plan ─→ next_wave ──dispatch──→ [convert_file × N] ──┐
                    ↑                                        │
                    └────────────────────────────────────────┘
                                ↓ (no waves left)
                              finish → END
```

`next_wave` bumps a counter and the conditional edge on it either fans out that
wave or ends the loop. The edge back from `convert_file` is the **join**:
LangGraph runs `next_wave` once after the whole super-step, not once per
branch, which is exactly the guarantee wave 2 needs — *nothing in wave 2 starts
until every file in wave 1 has been written.*

`plan` also copies the `copy`-action files (a plain `support/users.ts` with no
browser code in it) into the output tree before anything runs, because they are
**context**: a converted spec has to compile against something.

## 6. Using it

```bash
uv run s2p scan  samples/selenium-suite                 # the plan (9.1)
uv run s2p suite samples/selenium-suite --out out/9.2   # run it (9.2)

# a slice of a big suite, three at a time, with a cheaper model
uv run s2p suite ./e2e --out out/e2e --only 'pages/*.ts' --parallel 3 --model haiku
```

Real output, `--only` narrowed to one page object and the spec that imports it:

```
Suite samples/selenium-suite → out/9.2 · 2 file(s) · 2 wave(s) · 2 at a time · up to 3 attempt(s) each
Models: actor anthropic:claude-sonnet-5
wave  file                           result  laps  gates  critic  TODO  secs
1     pages/DynamicLoadingPage.ts    PASS    1     4/4    PASS    0     11
2     tests/dynamic-loading.spec.ts  PASS    1     4/4    PASS    0     8
2 file(s): 2 passed in 19.6s
Conversion tokens (whole suite): [6764 in / 974 out · cache write 0 · cache read 5380]
Critic tokens (whole suite): [8693 in / 173 out · cache write 0 · cache read 6602]
[wrote out/9.2]
```

Same contract as every other command: the table is on **stderr**, `--json`
puts one `s2p.suite-run/v1` document on **stdout**, and the exit code is 0 only
when every file passed outright, 1 when any needs review, 2 for a usage error.

### What parallel actually bought

Six files, three at a time, one trace:

| | |
|---|---|
| wall clock | **69.7 s** |
| per-file time added up | 111.1 s |
| wave 1 (3 page objects) | started within **8 ms** of each other |
| wave 2 (3 specs) | started **24 ms** after the slowest page object finished |
| LLM spans in the trace | 16 |

Wave 1 cost what its slowest file cost (`LoginPage.ts`, 47 s, two laps) rather
than the sum of all three. That is the whole point, and it is also the honest
limit: **a wave is only as fast as its slowest file.**

## 7. The sharp edges

* **A file that needs review is still written to disk.** It is the best
  available version, and the wave after it imports it. Only a run that produced
  no code at all writes nothing — and then its dependents are converted
  *without* that companion, which is a real quality hit and something 9.3's
  report has to say out loud.
* **One file crashing must not take the suite down.** `convert_file` catches
  everything a branch can throw and turns it into a `failed` outcome, because
  the alternative is losing eleven good conversions to one provider hiccup.
* **`--out` may not be the suite itself, or inside it.** Refused before a single
  model is built: the input to a conversion is not a place to write its output.
* **Nobody is there to ask.** `ask_risks` is off for every file in a suite run.
  The human-in-the-loop pause from [7.2](human-in-the-loop.md) needs a person at
  a terminal answering one question at a time; twelve parallel branches have
  nobody to interrupt. The risks are still detected and still reported.
* **A companion's TODO can travel.** In the live run above, `tests/login.spec.ts`
  came back carrying `pages/LoginPage.ts`'s open TODO, because the converted page
  object was in its prompt. The consolidated ledger in 9.3 will have to
  de-duplicate.
* **Long-term memory is shared, not per file.** One store, opened once, passed
  to every branch. Preferences you taught it apply across the suite.

### The rest of the suite travels too — as evidence, never as a gate input

Since 11.3b a job carries three more lists beside `context_paths`:
`repo_paths`, `caller_paths` and `pending_paths` (`repo_evidence` in
`suite_graph.py`). They are every other file the output tree will hold — read
from the output tree when it is already there, from the source tree when it is
still to be converted — ordered by how much they can tell this conversion:
the files that **import** the target first (T13: a page object cannot guess
the name its caller will use, so show it the caller), then the siblings
converted earlier in this run (the tenth page object should be converted the
way the first nine were), then the files still pending, then the copied
helpers. A file the plan skips is in neither tree and is not sent.

The single-file graph reads them at `intake` into `repo_files`, cuts them to
`S2P_REPO_CONTEXT_BYTES` (128 KB by default, 32 KB per file, `0` switches the
evidence off without a deploy), and `format_context` renders them after the
companions under `<caller_file>` and `<suite_file status="…">`, each section
telling the model that nothing inside is its task or its defect to report.
What it never does is hand one to a validator: `validate` reads
`context_files` only, so the compile gate sees exactly what it saw before and
every T14 fix stands. `tests/test_repo_context.py` pins both halves — what
each job is handed, and that a repo file which cannot compile does not reach
the gate. With no evidence sent, the prompt is byte-identical to before, which
is what keeps single-file mode and every eval row where they were.

## 8. What this step is *not*

The output tree is written and each file is validated **on its own**. Not here:
compiling the finished tree as one project, the aggregate scorecard, the
per-file parity ledger, the consolidated TODO(review) ledger, and the suite
report markdown. Those are [9.3](suite-report.md) — the `finish` node — and
they are built on the per-file outcomes this step produces.

## 9. Review checklist

1. A conditional edge can return a node name or a list of `Send`s. What can the
   second one do that the first cannot, and where does the number of branches
   come from?
2. Delete `Annotated[..., operator.add]` from `outcomes`. What breaks, when, and
   with what message?
3. Why does the sort into plan order happen in `ordered()`, at the point of
   reading, rather than in the `finish` node?
4. Wave 1 took 47 s but the three files in it took 9 s, 15 s and 47 s. Explain
   the 47, and say what would have to change about the *suite* — not the code —
   to make that number smaller.
5. `convert_file` writes a file whose report says `needs-review`. Give the
   argument for doing that, and the cost it imposes on the next wave.
