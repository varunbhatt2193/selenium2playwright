# Step 7.1 — short-term memory: threads and the checkpointer

*What this step adds, in one sentence: a conversion becomes a **conversation** —
you can come back and say "now use `getByTestId`" without re-pasting the file,
the previous output, or anything you said before.*

---

## 1. The problem, concretely

Everything up to Phase 6 was one-shot. `graph.invoke(...)` built a fresh state
dict, ran the nodes, returned a report, and the process exited. The state was
gone. So "make one small change to that conversion" meant re-running the whole
migration from the Selenium source and hoping the model landed in the same place.

That is not how anyone actually works. You convert a page object, look at it,
and *then* you know what you want: "our team uses `getByTestId`", "suffix page
object classes with `Page`". The agent needs to remember the file it converted,
the file it produced, and what you have told it.

## 2. The three words

**Checkpointer.** One object you hand to `compile()`. After that, LangGraph
writes a snapshot of the whole state to it after every *super-step* (each batch
of nodes that runs together). You write no save/load code; persistence is a
constructor argument.

**Thread.** A string you choose — `"login-page"`, `"demo-login"` — passed per
call in `config["configurable"]["thread_id"]`. Snapshots are filed under it.
Two thread ids are two independent conversations sharing one database.

**Resume.** Invoke the graph again with a thread that already has snapshots.
LangGraph loads the last one *first*, then merges your new input on top, then
runs from `START`. That merge is the whole trick: keys you don't pass keep the
value they had, so turn 2 can be nothing but `{"refinement": "..."}`.

```
turn 1   invoke({source_path: "LoginPage.ts"},   thread="login")   → snapshot
turn 2   invoke({refinement: "use getByTestId"}, thread="login")
              ↑ LangGraph loads the snapshot, so source_path is still there
```

`SqliteSaver` is the local implementation: a plain `sqlite3` file, no server.
LangGraph Platform swaps in a Postgres one in production and nothing in our
code changes — that is the point of programming to `BaseCheckpointSaver`.

Docs to read: <https://docs.langchain.com/oss/python/langgraph/persistence>

## 3. What we built

### `memory.py` — the whole persistence surface, ~90 lines

| function | job |
|---|---|
| `open_checkpointer(path)` | context manager: create the directory, open SQLite, yield a saver locked down as below |
| `thread_config(thread_id, **extra)` | the one config key that turns a stateless invoke into a conversation turn |
| `thread_state(graph, thread_id)` | the saved values, or `{}` for a thread that never ran |
| `list_threads(path)` | distinct thread ids in the file, for `--list-threads` |
| `strict_serializer()` | see the sharp edge below |
| `CHECKPOINT_TYPES` | every non-builtin class the state holds |

**The sharp edge.** Snapshots are msgpack. Builtins rebuild themselves;
anything else — our `ConversionResult`, `ValidationReport`, `Classification` —
is stored as *module + class name + fields*. By default LangGraph reads those
permissively: it imports whatever class the file names, and prints a
deprecation warning saying this will be blocked in a future version. So a
thread database is, by default, a list of classes to import.

`with_allowlist(...)` alone does **not** fix that: it derives an allowlist from
the serializer you already have, and "everything is allowed" plus an allowlist
is still everything. The library's strict mode is reached through the
`LANGGRAPH_STRICT_MSGPACK` environment variable, read once at import — too late
and too global to depend on. So `strict_serializer()` asks for it directly
(`JsonPlusSerializer(allowed_msgpack_modules=None)`) and `CHECKPOINT_TYPES` is
layered on top. Now the database is data, the warning is gone, and the day
LangGraph flips the default nothing here changes.

The failure mode is worth knowing because it is **quiet**: a type you forgot to
list is not an error on read. It comes back as the plain dict it was encoded
from, and the `AttributeError` arrives much later at `report.result`. That is
why `tests/test_memory.py` round-trips every type the state actually holds, and
separately proves an unlisted one degrades to a dict.

### Four new state keys

```python
refinement: str                    # input: this turn's instruction ("" on turn 1)
turn: int                          # invokes on this thread; 1 on a fresh one
conventions: list[str]             # every instruction given, oldest first
baseline: ConversionResult | None  # the previous turn's output — this turn's start
```

### `intake` becomes the turn boundary

This is where one turn hands over to the next. On a resumed thread the
checkpointer has already put the previous `report` and `conventions` in state,
so `intake`:

- re-reads the source **from disk** (the file is the truth, not the snapshot);
- moves the previous turn's result into `baseline`;
- appends a new `refinement` to `conventions` (deduplicated, then cleared so it
  isn't left pending);
- clears everything that belonged to the finished turn — draft, validation,
  critique, `iteration` — so the new turn starts honest with a fresh budget of
  three attempts.

### `convert` now has three ways in, one node

| situation | trailing message |
|---|---|
| first draft | none |
| repair inside this turn | `revision_feedback` — the draft plus its findings and the critic's fixes (5.2) |
| first attempt of a later turn | `refinement_feedback` — the previous turn's accepted file |

`refinement_feedback` is deliberately *not* `revision_feedback`: nothing is
broken. The instruction is "carry this accepted file across to satisfy a new
rule", not "start the migration again", which would risk losing decisions
already made and reviewed.

### Standing instructions ride on every call

`format_conventions()` renders the numbered list into a message placed **after**
the cached system prefix (so it costs no cache miss) and **before** the repair
feedback. Two consequences that matter:

1. A repair lap on turn 3 still carries the rule you gave on turn 2 — the loop
   can never quietly undo your convention while fixing a compile error.
2. The **critic** gets the same block. Without it the reviewer would flag your
   own convention as a defect; with it, the reviewer checks the rule was
   actually applied *and* that applying it cost no test or assertion.

The block's own wording keeps honesty ahead of obedience: an instruction never
licenses deleting a test, weakening an assertion, or inventing a selector. If it
can't be followed honestly, apply what you can and leave a `TODO(review)`.

### CLI

```bash
# turn 1 — name the file once
uv run python -m selenium2playwright.graph samples/selenium-suite/pages/LoginPage.ts \
    --thread login --out out/LoginPage.ts

# turn 2 — name nothing
uv run python -m selenium2playwright.graph --thread login \
    --refine "use getByTestId for every form field"

uv run python -m selenium2playwright.graph --list-threads
```

`source` is now optional; omit it and `--thread` supplies it. The thread also
remembers `--out`, so turn 2 rewrites the same file in place. Without
`--thread`, nothing is written and nothing is restored — the stateless graph
every earlier phase and the eval runner still use is completely unchanged.

## 4. Live proof

`uv run python scripts/demo_memory.py` runs both turns against the real model —
nothing injected. The recorded run (Sonnet actor and critic, thread
`demo-login`, artifacts in `out/7.1/`):

| | turn 1 | turn 2 |
|---|---|---|
| what it was given | the source path | `{"refinement": "Use getByTestId() for every form field locator, with the test id equal to the original element id."}` |
| attempts | 3 | 2 |
| gates | 4/4 pass | 4/4 pass |
| critic | revise ×3 | revise, then **pass** |
| status | needs-review (attempt cap) | needs-review (open TODOs) |

Traces: [turn 1](https://smith.langchain.com/o/32ac11b4-3e72-4765-a59b-5dc1bcd32cbe/projects/p/9197382d-0822-473f-bbe6-71f4af57ac3e/r/82f5f457-1cdd-416e-ba48-f77a45b00382) ·
[turn 2](https://smith.langchain.com/o/32ac11b4-3e72-4765-a59b-5dc1bcd32cbe/projects/p/9197382d-0822-473f-bbe6-71f4af57ac3e/r/f45305f4-8419-4cd6-92c2-6194641344d2).

Turn 2's `intake` event shows the memory doing its job:

```json
{"node": "intake", "turn": 2, "attempt": 0,
 "conventions": ["Use getByTestId() for every form field locator, with the test id equal to the original element id."],
 "from_previous_turn": true}
```

And the diff `turn1.ts` → `turn2.ts` shows the honesty clause holding under a
user instruction — the interesting part of the run:

```diff
-    this.usernameInput = page.getByLabel("Username");
-    this.passwordInput = page.getByLabel("Password");
-    this.loginButton   = page.getByRole("button", { name: "Login" });
-    this.flashMessage  = page.locator("#flash");
+    this.usernameInput = page.getByTestId("username");
+    this.passwordInput = page.getByTestId("password");
+    // TODO(review): the original locator for the login button was
+    // By.css("button[type='submit']") — it has no element id, so the standing
+    // instruction ("test id equal to the original element id") cannot be
+    // applied literally. Kept the CSS selector as the closest faithful
+    // conversion (rule 8). Confirm whether the button carries a data-testid.
+    this.loginButton   = page.locator("button[type='submit']");
+    this.flashMessage  = page.getByTestId("flash");
```

Three fields took the instruction. The submit button had no id to derive a test
id from, so the agent **did not invent one** — it kept the faithful CSS locator
and said why. It also flagged that the `getByTestId` premise itself is
unverified: the Selenium source proves `By.id`, not the presence of matching
`data-testid` attributes. Both land in the TODO ledger, and the run ends
`needs-review` rather than pretending to be finished.

That is the behaviour the block's wording was written for: your instruction wins
on style, never on truth.

## 5. What this step is *not*

- **Not cross-thread memory.** A second thread starts clean; a convention taught
  in thread 1 does not apply in thread 2. That is step 7.3 (the `Store`), and
  `tests/test_memory.py::test_a_separate_thread_starts_clean` pins the boundary.
- **Not human-in-the-loop.** Nothing pauses mid-run to ask you a question yet —
  that is `interrupt()` in step 7.2.
- **Not a behaviour change for anything else.** `build_graph()` with no
  checkpointer is byte-identical to Phase 6, and so is the prompt when a thread
  has no conventions — both are pinned by tests, so the eval baselines still hold.

## 6. Review checklist

Questions worth being able to answer before 7.2:

1. Why does `intake` re-read the source from disk instead of restoring it?
2. Why is `iteration` reset per turn but `conventions` accumulated?
3. Why do the conventions go *after* the system message rather than into it?
4. Why does the critic need the conventions too?
5. What happens if you add a new Pydantic type to `ConversionState` and forget
   `memory.CHECKPOINT_TYPES`? (The answer is the uncomfortable one.)
