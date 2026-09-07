# Step 9.3 — assemble: what the whole folder adds up to

> **In one line:** when the last wave is done, the tree is compiled **as one
> project**, the per-file results are added up, the public API of every file is
> diffed against its source, every `TODO(review)` is gathered into one list, and
> all of it is written next to the code as `conversion-report.md`.

Step [9.2](suite-fanout.md) converted a folder and printed a row per file. This
step answers the question those rows cannot: **is this folder something I can
hand to somebody?**

---

## 1. Why a green row per file is not an answer

Every file in a 9.2 run went through four gates. Every gate was real: the pinned
`tsc`, the pinned ESLint, a residue search, a static parity comparison. And
every one of those verdicts is a **local** claim — this file compiled, on its
own, against the companions it happened to import.

That leaves a hole you can drive a suite through:

```ts
// pages/LoginPage.ts — compiles perfectly, on its own
export class LoginPage {
  async login(user: string, password: string): Promise<void> { /* … */ }
}

// tests/login.spec.ts — compiles perfectly, on its own
import { LoginPage } from "../pages/LoginPage";
await new LoginPage().login("tomsmith");        // one argument
```

Two green files. One broken folder. The spec was converted *before* the page
object grew a second parameter, or it was converted from a source where `login`
took one — either way, no per-file gate can see it, because no per-file gate
ever had both files in front of it at the same time.

So the first thing 9.3 does is compile the **delivered tree**: every converted
file, every copied support file, and every import between them, in one `tsc
--noEmit`. It is the only check in the project that can see between files, and
it is the one that decides the exit code:

```python
exit_code = 0 if not failed and built.compiles else 1
```

Twelve passing rows and a tree that does not build is an **exit 1**. That is
the whole point.

## 2. The four things `assemble.py` produces

| # | Fact | Where it comes from |
|---|---|---|
| 1 | **Whole-tree compile** | `tsc --noEmit` over `read_tree(out_root)` — the same pinned compiler gate 1 uses, pointed at everything at once |
| 2 | **Scorecard** | the per-file `FileOutcome` rows, added up: statuses, per-gate tallies, laps, seconds, tokens |
| 3 | **Parity ledger** | the TypeScript parser, over source and converted side by side: what public API was kept, renamed, or removed |
| 4 | **TODO(review) ledger** | the comments in the written code + what each conversion reported, de-duplicated |

All four are deterministic. **No model runs in this step.** Assembly is
evidence, not opinion — which is exactly why it can be trusted to contradict
the twelve green rows above it.

## 3. The parity ledger — the half no gate was watching

Gate 4 (the [parity gate](parity-gate.md)) compares **tests and assertions**
inside one file. It says nothing about the *surface* — the methods and exported
names other files call. That surface is what a page object exists for, and
losing a method from it is exactly the kind of quiet damage a conversion does.

`sandbox/members.cjs` parses both sides (parse only — it never imports or runs
submitted code) and reports the public API of each file: class members that are
not `private`/`protected`/`#private`, `public` constructor parameter properties,
and top-level exported functions and constants. Then each source name is matched
against the converted ones:

* **kept** — the name is still there.
* **renamed** — the name is gone, but one new name is close enough to be the
  same member under a new convention.
* **removed** — gone, with nothing that looks like it. If the model's own
  `notes` or `todos` mention the name, that sentence is quoted as the reason;
  if nothing does, the report says **no reason given**, which is the loudest
  line in the document.

### How a rename is guessed, and where the guessing stops

The commonest real rename in this conversion is not a rewording, it is an idiom
change: Selenium's `getFlashText()` returns a string; Playwright's
`flashMessage` is a locator you assert on. So names are compared by their
**stem** — accessor prefixes (`get`, `is`, `waitFor`, …) and type-ish suffixes
(`Text`, `Value`, `Element`, …) stripped off — and if one stem contains the
other, that is treated as a rename.

```
getFlashText   → flashMessage      stem "flash" ⊂ "flashmessage"   rename
getHeadingText → heading           stem "heading" = "heading"      rename
open           → goto              stem "open" vs "goto" = 0.25    NOT a rename
```

`open → goto` is a real rename that this will report as a removal plus an
addition. That is deliberate. **A missed rename is noise; an invented one is a
hidden loss** — it turns "this method is gone" into "this method is fine". The
threshold is set so the report over-reports removals and lets you decide.

Two more rules keep the guessing honest:

* Members only pair **inside the same class** (`LoginPage.` is not evidence of
  anything), and a class rename is detected first, so `LoginPage` →
  `LoginPageObject` does not read as every member disappearing at once.
* **Tests and members are matched in separate pools.** A lost test can never be
  explained away as a renamed method.

## 4. The consolidated TODO ledger — playbook rule 25

The playbook tells the model to place `TODO(review)` in the code for anything it
could not resolve, and to repeat every one of them in its report. Rule 25 asks
for one consolidated list at the end.

The de-duplication is not cosmetic. Observed live in 9.2: the converted
`pages/LoginPage.ts` was handed to `tests/login.spec.ts` as context, and the
spec came back reporting the page object's TODO **as its own**, with the path
glued to the front:

```
pages/LoginPage.ts: TODO(review): confirm the flash locator
```

Two reports, one task. The ledger strips comment markers, the `TODO(review)`
word and a leading `path.ts:` prefix, and treats what is left as the identity of
the task — so it lands as one line with every place it was seen:

```
1. confirm the flash locator · pages/LoginPage.ts:14, tests/login.spec.ts
```

It reads from **both** sources on purpose: the comments actually in the code
(with line numbers — what a reviewer will hit) and the `todos` the model
reported. A task in one but not the other is worth knowing about.

## 5. Where it runs

Inside the graph, in the `finish` node — not in the CLI afterwards:

```
START → plan ─→ next_wave ──dispatch──→ [convert_file × N] ──┐
                    ↑                                        │
                    └────────────────────────────────────────┘
                                ↓ (no waves left)
                              finish → END
                                 └── assemble → conversion-report.md
```

The graph produced the tree, so the graph is what says whether the tree holds
together — and the whole-tree compile shows up in the LangSmith trace right
beside the twelve conversions that made it necessary.

## 6. Using it

```bash
uv run s2p suite samples/selenium-suite --out out/9.3
uv run s2p suite samples/selenium-suite --out out/9.3 --report docs/demo-report.md
uv run s2p suite samples/selenium-suite --out out/9.3 --json > run.json
```

Nothing new to learn: the report is written on every run. `--report FILE` moves
it; `--json` puts the whole assembled document on stdout as
`s2p.suite-report/v1`, which is every key of 9.2's `s2p.suite-run/v1` plus
`tree`, `scorecard`, `parity` and `todos`. A superset, deliberately — anything
that read the run document still finds its keys where they were.

## 7. The live run

All twelve sample files, four at a time, Sonnet, on 2026-09-07. The full report
it produced is committed verbatim at
[phase-9.3-report.md](phase-9.3-report.md); this is the tail of the terminal:

```
12 file(s): 9 passed · 3 needs-review in 83.1s
Whole tree: 12 file(s) compile together, no errors
Public API: 20 kept · 8 renamed · 0 removed
  pages/AlertsPage.ts: AlertsPage.getResultText — now AlertsPage.resultMessage
  pages/IframePage.ts: IframePage.getEditorText — now IframePage.editorBody
  …
TODO(review): 2 task(s) still open
  1. consider exposing a helper (e.g. a static method or a second constructor arg… · pages/WindowsPage.ts:11
  2. verify username/password fields have accessible labels on the-internet.hero… · tests/login.spec.ts, pages/LoginPage.ts:5
[wrote out/9.3]
[wrote out/9.3/conversion-report.md]
```

Three things in there are worth pointing at.

**The whole tree compiles**, and now that is a *reported* fact rather than
something run by hand — the twelve converted files were compiled together, as
one project, by the run that produced them.

**Twenty public names survived and eight were renamed, none removed.** Every one
of the eight is the same rename: `getResultText` → `resultMessage`,
`getFinishedText` → `finishedText`, `getHeadingText` → `heading`. That is the
Selenium-to-Playwright idiom shift showing up as data — a Selenium page object
returns strings, a Playwright page object exposes locators — and it is the kind
of sentence you want in a report to an SDET lead, because it is the difference
between "the model rewrote my page objects" and "the model applied one
convention, eight times, and lost nothing."

**TODO number 2 names two files.** `pages/LoginPage.ts` wrote it, and
`tests/login.spec.ts` reported it as its own because the converted page object
was in its prompt — the exact duplication [9.2](suite-fanout.md) observed and
left for this step. One task, one line, both places.

The trace (`01a07aa0-d597-75e1-bdfb-886593881b7e`) is one run holding 557
others and 32 model calls. In it you can watch `--parallel 4` do its job: wave
1's first four branches start within **17 ms** of each other, the fifth starts
**16 ms** after the first one finishes, and wave 2 starts **28 ms** after the
slowest file of wave 1. The `finish` node — `tsc` over twelve files, two
TypeScript-parser passes, both ledgers and the report written — costs **0.63 s**
of the 83.1 s.

## 8. Sharp edges

* **Unknown is not the same as fine.** If the sandbox is missing or `tsc` times
  out, the run is not lost — the failure is reported as text and every other
  number still holds — but `compiles` is `False` and the exit code is 1. A gate
  that did not run has not passed.
* **The report is written even when the run is bad.** A refused or failed file
  gets a ledger entry saying *why* there is nothing to compare, not a silent gap.
* **Parity is still syntactic.** A kept test with the same assertion count is
  not proof that it asserts the same thing, and nothing here executes the
  converted code.
* **Only top-level declarations are inventoried.** A class defined inside a
  function or a namespace is not part of the compared surface.
* **The tree is whatever is in `--out`.** Point two runs at the same folder and
  the second compiles both. That is correct — it is the folder being delivered
  — but it is worth knowing.

## 9. Review checklist

1. Twelve files each pass all four gates. Give a concrete example of a folder
   that is still broken, and name the check that catches it.
2. Why is `assemble` a node in the graph rather than something `present_suite`
   does after `invoke` returns? Name one thing that would be lost.
3. `getFlashText` → `flashMessage` is called a rename; `open` → `goto` is called
   a removal plus an addition. Both are renames in reality. Why is the second
   answer the safer one to be wrong about?
4. The same `TODO(review)` is reported by a page object and by the spec that
   imports it. Explain how the spec got it, and what the ledger uses as the
   identity of a task.
5. The whole-tree compile cannot run (no `node_modules`). Every file passed.
   What exit code does `s2p suite` give, and what is the argument for it?
