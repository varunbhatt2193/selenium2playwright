# Step 7.3 — long-term memory: teach it once, not once per conversation

> **In one line:** the agent now keeps the preferences you teach it in a store
> of its own, and pulls the few that matter for the file in front of it — so a
> rule taught on Monday's page object applies to Wednesday's spec, in a fresh
> conversation, with nothing typed.

Read [short-term-memory.md](short-term-memory.md) first — this is the other half
of that story — and [human-in-the-loop.md](human-in-the-loop.md) for the node
this one sits next to.

---

## 1. The problem

Step 7.1 gave every conversation a memory. Tell it *"use getByTestId for form
fields"* on the `--thread login` conversation and the rule sticks: it survives
the next turn, and every repair lap inside that turn.

Then you convert the next file. New thread, new conversation — and the rule is
gone. Not because anything failed, but because the checkpointer is filed under
`thread_id` and this is a different thread. So you type it again. And again on
the file after that, and on Wednesday, and next month when someone new runs it.

A convention is not a fact about one file. It is a fact about **how this team
writes Playwright**, and it should outlive the conversation it was mentioned in.

## 2. The second database

LangGraph's answer is a **store**. It is the same idea as the checkpointer — an
object you hand to `compile()`, which nodes are then given — with a different
scope and a different key:

|  | checkpointer (7.1) | store (7.3) |
|---|---|---|
| holds | one conversation's whole state | preferences you asked it to keep |
| keyed by | `thread_id` | a namespace tuple + a key |
| lives | until you stop using that thread | until you forget it |
| answers | "what did we do last turn?" | "what does this person always want?" |

```python
store.put(("conventions", "varun"), key, {"text": "In test specs, wrap each action in a named test.step() block"})
store.search(("conventions", "varun"), query=<a description of this file>)
```

The namespace is just a folder: `("conventions", user)` so a shared database in
production can never mix two people's preferences. The key is a hash of the
text, so teaching the same rule twice updates one memory instead of collecting
duplicates.

Both databases are SQLite files here and both are swapped for Postgres on
LangGraph Platform later (Phase 10). Neither knows about the other: a one-off
run with no `--thread` still recalls what you taught it last week.

## 3. Why recall needs embeddings

Thirty remembered preferences are useless if all thirty go into every prompt.
They cost tokens, they contradict each other, and the model has to guess which
ones are about the file in front of it. So the store is asked a question —
*what do I know that matters for THIS file?* — and answers with the closest few.

That comparison is what an **embedding** is. An embeddings model turns a piece
of text into a long list of numbers (1536 of them for
`openai:text-embedding-3-small`) positioned so that texts with similar meanings
end up pointing in similar directions. Closeness is the cosine of the angle
between two of those directions: 1.0 is the same direction, 0 is unrelated. No
keyword has to match — *"prefer role-based selectors"* is close to a file full
of `By.css` because the meaning is close, not because a word repeats.

That is the whole mechanism. The store embeds each memory when you write it,
embeds the query when you ask, and returns the nearest.

Two things follow, and both cost me a live experiment to learn properly:

- **Scores have no universal meaning.** 0.4 is not "40% relevant". A threshold
  has to be measured against your own data — §5.
- **Vectors from two models are not comparable.** Switching the embeddings
  model does not degrade recall, it makes it nonsense, and nothing looks broken.

## 4. What this step built

**`store.py`** — the memory itself. `remember` / `forget` / `memories` /
`recall`, a frozen `Memory(key, text, score, created)`, and `open_store()`,
which builds the SQLite store with its vector index and records which
embeddings model built it.

**`recall_query()`** — what we ask the store. Not the file: a *profile* of it.

```
mocha test spec upload.spec.ts
tests: Upload; uploads a created file and verifies its name
selenium api: wait, upload
```

```
page object LoginPage.ts
classes: login page
methods: open, login, get flash text
locators: css, id
selenium api: send keys, click, get text, wait, until
elements: button[type=, flash, password, username
```

Kind of file, name, class, test titles, method names, locator strategies,
element ids — with `camelCase` split into words, because embeddings read words,
not identifiers. §5 shows the measurement that forced this shape.

**A `recall` node**, between `intake` and `risk_review`:

```
START → intake → recall → risk_review → convert → validate → critic ⇄ convert
             ↘ refuse                                             ↘ assemble → END
```

It does two things, in this order for a reason. A preference given *now*
(`--remember`) is written to the store and always sent — the user just said it,
so it does not have to survive a similarity threshold to be obeyed this run.
Then the store is asked which *older* preferences are close enough to this file
to be worth the tokens. Anything already standing on the thread is excluded, so
a rule you repeated locally reaches the model once, not twice.

**A prompt block** — `REMEMBERED PREFERENCES`, ahead of the thread's standing
instructions and the human's risk answers, so the order the model reads is
general → specific → immediate. It says plainly that these were selected by
similarity rather than chosen for this file, and that the model should apply the
ones that genuinely fit. The critic gets the same block, plus one rubric line:
a preference that does not apply here is **correctly ignored, not a defect**.

**The CLI:**

```console
$ ... --remember "In test specs, wrap each action in a named test.step() block"
remembered [ada40feb123c33a0] In test specs, wrap each action in a named test.step() block

$ ... --memories
79a8696203943edb  Name page object classes <Feature>Page and give each one an open() method
ada40feb123c33a0  In test specs, wrap each action in a named test.step() block

$ ... samples/selenium-suite/tests/upload.spec.ts pages/UploadPage.ts --out ...
[selenium · mocha · typescript] selenium-webdriver mocha tests in TypeScript
Long-term memory: applying 1 of 2 remembered preference(s)
  ada40feb123c33a0 (score 0.32) In test specs, wrap each action in a named test.step() block
Conversion: passed (1/3 attempts) — All four gates and the critic passed; no open TODO(review) items.
```

`--forget <key>` deletes one, `--user` picks whose memories, `--no-recall` turns
the whole thing off for a run. Nothing is remembered unless you say
`--remember`: the agent never decides on its own that something was worth
keeping. And when a preference exists but was *not* close enough, the run says
so — `2 remembered preference(s), none close enough to this file (minimum score
0.29)` — because "why didn't it use my rule?" deserves an answer on screen.

Off by default everywhere else: with no store, the `recall` node returns
nothing, the prompt has no such block, and every earlier phase and the whole
eval suite send byte-identical bytes.

## 5. Calibrating the number nobody can guess

`MIN_SCORE` is the only real parameter in this step, and it cannot be reasoned
out. `scripts/calibrate_recall.py` measures it: eight labelled preferences —
five that genuinely apply to some of the six sample files, three about the CI
pipeline and the release process that must never reach a prompt — scored against
every file.

**The first result was that my query was wrong.** With the obvious query (paste
the head of the file in), every memory scored the same mid-range number and no
threshold separated anything:

```
naive query — the head of the file
lowest score that genuinely applies : 0.2962
loudest unrelated memory            : 0.3044   ← louder than the real ones
```

A page of raw TypeScript embeds to *"some Selenium code"*. Replacing it with the
profile in §4 is what made the ranking work — the file's *names* are where its
meaning lives:

```
store.recall_query — a profile of the file
file                      testids     fixtures       naming      uploads  steps-vague  steps-named           ci      release      support
pages/LoginPage.ts         0.3697       0.3413       0.4783       0.2183       0.2298       0.1949       0.1453       0.0479       0.1933
pages/AlertsPage.ts        0.3587       0.2920       0.4234       0.2437       0.2437       0.1936       0.2115       0.0622       0.2392
pages/UploadPage.ts        0.3464       0.2632       0.5675       0.4113       0.2108       0.1622       0.2209       0.0962       0.2319
tests/login.spec.ts        0.3756       0.5046       0.2319       0.2003       0.2978       0.3353       0.1987       0.0867       0.2500
tests/alerts.spec.ts       0.3389       0.3939       0.2146       0.1985       0.3118       0.3252       0.2284       0.0931       0.2961
tests/upload.spec.ts       0.3325       0.3802       0.2886       0.3520       0.2813       0.3199       0.2714       0.1314       0.2610
```

Read down the columns: `naming` (a rule about page object classes) wins on the
three page objects and loses on the three specs; `fixtures` does the exact
opposite; `uploads` peaks on the two upload files; the three noise memories stay
at the bottom everywhere.

**The second result was that no single threshold is perfect, and saying so is
part of the answer.** The lowest genuinely-applicable score (0.2813) is *below*
the loudest unrelated one (0.2961) — the bands overlap. So the script stops
asking for a magic number and measures the thing that matters: the shortlist
each file would actually be sent, at `MIN_SCORE = 0.29` with a cap of 3.

```
shortlists at MIN_SCORE 0.29, limit 3:
  pages/LoginPage.ts    naming@0.478, testids@0.370, fixtures@0.341
  pages/AlertsPage.ts   naming@0.423, testids@0.359, fixtures@0.292
  pages/UploadPage.ts   naming@0.568, uploads@0.411, testids@0.346
  tests/login.spec.ts   fixtures@0.505, testids@0.376, steps-named@0.335
  tests/alerts.spec.ts  fixtures@0.394, testids@0.339, steps-named@0.325
  tests/upload.spec.ts  fixtures@0.380, uploads@0.352, testids@0.333
applicable memories recalled : 11 of 15
unrelated memories recalled  : 0  (must be 0)
```

That is the honest contract: **the gate keeps noise out — that part is
enforced — and the ranking plus the cap of three decide the rest.** Which of
three plausible preferences actually applies to this file is the model's job,
which is exactly what the prompt asks it to do.

**The third result is the one worth remembering.** Two of the eight memories are
the same rule in different words:

| | wording | login.spec | alerts.spec | upload.spec |
|---|---|---|---|---|
| `steps-vague` | "Wrap the actions inside every test in test.step() calls named after what the step does, so the HTML report reads like a scenario" | 0.298 | 0.312 | 0.281 |
| `steps-named` | "**In test specs**, wrap each action in a named test.step() block" | 0.335 | 0.325 | 0.320 |

Four words naming the kind of file the rule is about moved it further than any
threshold change could. Every miss in the 11-of-15 above is the vague twin.
**How you word a memory decides whether it comes back** — which is why the CLI
prints what it applied, and why this doc says so out loud instead of pretending
the number is doing more work than it is.

## 6. Four sharp edges

**`fields` is silently ignored by `SqliteStore`.** The documented index config —
and `SqliteStore`'s own docstring — say `{"dims": …, "embed": …, "fields":
["text"]}`. Its implementation reads `text_fields`, falls back to `"$"` (the
whole document) and embeds the entire JSON, including each memory's `source`
provenance. No error. The symptom was the same sentence scoring 0.3238 in one
database and 0.3954 in another, because they had been written from different
files. `open_store()` now passes both keys, and a test pins that a memory's
score does not depend on where it was written.

**`store: BaseStore | None` turns the whole feature off, silently.** LangGraph
decides whether to hand a node the store by matching the parameter *by name and
by the literal text of its annotation*, against a fixed list containing
`"BaseStore"` and `"Optional[BaseStore]"` — and nothing else. This file has
`from __future__ import annotations`, so every annotation is a string, and the
modern spelling matches none of them. The node still runs. `store` is just
always `None`. Everything looks fine and nothing is ever recalled. The node is
therefore annotated `Optional[BaseStore]`, with a comment and a test that pin
the spelling.

**A memory written without an index can never be found again.** Vectors are
computed on write, not on read: put an item into a store opened without
embeddings, later open it *with* embeddings, and the item is invisible to every
search, forever. So `--remember` refuses to run when the configured embeddings
model cannot be loaded, and says which two ways out there are. Reading degrades
instead — it falls back to the most recent memories and prints that it did.

**`SqliteStore.setup()` needs `isolation_level=None`.** Its migrations open
explicit transactions, and sqlite3's default implicit `BEGIN` collides with them:
`cannot start a transaction within a transaction`, on the very first call.

## 7. Live proof

`caffeinate -i -s uv run python scripts/demo_store.py` — Sonnet actor and
critic, artifacts in `out/7.3/`. Three conversations, in order:

1. **taught** — convert `pages/LoginPage.ts` in thread `monday`, teaching
   *"In test specs, wrap each action in a named test.step() block"* on the way
   past. Three more memories are filed directly: the vague twin above, one about
   page object naming, and one about the CI pipeline publishing to S3.
2. **control** — convert `tests/upload.spec.ts` with **no store at all**.
3. **recall** — convert the same file in a brand-new thread `wednesday`, told
   nothing whatsoever.

All three passed four gates and the critic on the first attempt. The only
difference between arms 2 and 3 is that long-term memory exists:

```
4 preference(s) remembered; the fresh conversation recalled 1:
  0.320  In test specs, wrap each action in a named test.step() block
CI memory (noise) recalled: False
memory changed the output: True (25 changed lines)
```

```diff
--- control
+++ recall
-      await writeFile(filePath, "Selenium to Playwright evaluation fixture.\n", "utf8");
-      await uploadPage.open();
-      await uploadPage.upload(filePath);
-      await expect(uploadPage.heading).toHaveText("File Uploaded!");
-      await expect(uploadPage.uploadedFilename).toHaveText(filename);
+      await test.step("create fixture file", async () => {
+        await writeFile(filePath, "Selenium to Playwright evaluation fixture.\n", "utf8");
+      });
+      await test.step("open upload page", async () => {
+        await uploadPage.open();
+      });
+      await test.step("upload file", async () => {
+        await uploadPage.upload(filePath);
+      });
+      await test.step("verify heading text", async () => {
+        await expect(uploadPage.heading).toHaveText("File Uploaded!");
+      });
+      await test.step("verify uploaded filename", async () => {
+        await expect(uploadPage.uploadedFilename).toHaveText(filename);
+      });
```

Nobody typed anything about `test.step` in that conversation. Three of the four
memories — including the vague twin of the one that was applied, and the page
object rule that has no business in a spec — stayed behind. The cost of
recalling was 394 extra actor tokens (4,571 → 4,965) and one embeddings call.

Receipts: `out/7.3/demo-receipt.json` (scores, tokens, LangSmith URLs),
`out/7.3/recall-calibration.json`, `out/7.3/recall.diff`.

## 8. What this step is not

- **Not automatic learning.** Nothing is stored unless you say `--remember`. The
  agent never decides on its own that a passing remark was worth keeping.
- **Not RAG over your codebase.** The store holds sentences you wrote about how
  you want code written, not chunks of the code itself.
- **Not a relevance judge.** The gate keeps noise out; the model decides what
  actually applies, and is told that ignoring an unfitting preference is correct.
- **Not authority over the truth.** A remembered preference has exactly the same
  standing as a standing instruction: it settles style, never facts. It cannot
  license deleting a test, weakening an assertion, or inventing a selector, and
  an instruction that cannot be followed honestly comes back as a `TODO(review)`.
- **Not shared yet.** One local SQLite file per user; the Postgres store and a
  real user identity arrive with deployment in Phase 10.

## 9. Review checklist

Five questions worth being able to answer before 7.4:

1. Why is a preference given with `--remember` sent to the model this run
   without being scored, while every older one has to clear `MIN_SCORE`?
2. `recall` runs before `risk_review`, which can pause the graph for a human.
   Why is that the right order, and what would break if the two swapped?
3. The calibration reports "11 of 15 applicable memories recalled" and calls
   that a pass. What is the actual pass condition, and why is 15 of 15 not it?
4. Why does the critic need to be told that an ignored remembered preference is
   not a defect — what would it otherwise do on the very next repair lap?
5. `open_store` refuses to open a database that was built with a different
   embeddings model. Why is refusing better than re-embedding everything, or
   than simply carrying on?
