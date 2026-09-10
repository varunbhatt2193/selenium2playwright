# Step 9.1 — the suite scan: read the whole folder before converting any of it

> **In one line:** point `s2p scan` at a Selenium suite and it tells you what
> every file is, what can be converted, and in what **order** — a plan built
> from the import graph, with no model called and no file written.

This is the first step of Phase 9 (suite mode). Steps [9.2](suite-fanout.md) and
9.3 turn this plan into parallel conversions and one report; nothing here does
any converting yet.

---

## 1. The problem

Everything before this converts **one file**. You hand it
`pages/LoginPage.ts` and it hands you Playwright back.

A real suite is a folder. Point the same command at it and three questions
appear that a single file never asked:

1. **What is each file?** A page object, a test, a plain data helper with no
   browser code in it at all, or something we cannot convert (a Cypress spec, a
   Java file, a Playwright file somebody already converted).
2. **In what order?** `tests/login.spec.ts` starts with
   `import { LoginPage } from "../pages/LoginPage"`. If we convert the spec
   first, it is a spec written against a page object that does not exist yet.
   The page object has to go first.
3. **What can run at the same time?** Six page objects that import nothing from
   each other have no reason to be converted one after another.

None of those three needs a model to answer. They need a directory walk, the
import lines, and a sort.

## 2. Waves

The order question has a standard answer. Draw an arrow from each file to the
files it imports; that is a **dependency graph**. Ordering it so nothing comes
before what it needs is a **topological sort**.

We do not want a single line of 12 files, though — we want the *layers*:

```
wave 1   pages/AlertsPage.ts  pages/LoginPage.ts  pages/UploadPage.ts  …
             ↑                     ↑                    ↑
wave 2   tests/alerts.spec.ts  tests/login.spec.ts  tests/upload.spec.ts  …
```

A **wave** is one layer: wave 1 is every file that imports nothing else in the
suite, wave 2 is every file whose in-suite imports are all in wave 1, and so on.

The property that matters is this: **nothing in a wave depends on anything else
in that wave.** That is exactly the licence to run them all at once, which is
what step 9.2 will do with LangGraph's `Send`. The waves are also the sync
points — wave 2 may not start until wave 1 has finished, because that is where
its inputs come from.

The algorithm is Kahn's, written out in `plan_waves`: take everything with no
outstanding dependency (that is a wave), remove it from everyone else's list,
repeat. It runs over **strongly connected components** rather than over files
(`strongly_connected`, Tarjan's algorithm), so an import cycle is one node of
the layering: its members convert together, and the files behind it still wait
their turn. See §6 for why that matters on a real repository.

## 3. Four kinds, three actions

`classify.py` already answers *"can I convert this one file?"*. In a folder its
answer needs splitting, because one of its "no"s is not a refusal at all:

| kind | what it is | action | why |
|---|---|---|---|
| `page-object` | Selenium TS, no test runner | **convert** | the reusable half of the suite |
| `test` | Selenium TS with `describe`/`it` | **convert** | the tests themselves |
| `support` | TS/JS with no automation library in it | **copy** | test data, helpers — nothing to convert, but the suite needs it |
| `unsupported` | Cypress, WebdriverIO, Java, already-Playwright | **skip** | named out loud with the reason, never silently dropped |

`support/users.ts` is the interesting one. Asked on its own,
`classify()` says *"no recognised automation library — unsupported"*, which is
the right answer to *"convert this file"*. Inside a folder the same fact means
something much less alarming: **there is nothing here to convert, so carry it
across unchanged**. `suite.decide()` is where those two readings part company.

Copied and skipped files are in **no wave** — a wave is a conversion schedule,
and neither of them is converted.

## 3a. Whole suite or nothing

A suite is metered **per file**: one click on a forty-file upload is forty model
calls, and on the hosted demo they all land on one card. There used to be a
per-kind ceiling here (`S2P_SUITE_MAX_TESTS`, `S2P_SUITE_MAX_PAGE_OBJECTS`) that
converted a slice of a big folder and copied the rest across. It is gone, for
two reasons that showed up the moment it met real repositories:

* **The slice it chose was the wrong end.** It walked the wave order, and wave 1
  is the *leaves* — driver factories, config readers, wrapper libraries. On
  `imranwijaya/selenium-typescript-example` it converted six files and not one
  of them was a test.
* **A half-converted folder is not a result.** Half the suite calls the new API
  and half still calls Selenium, and the person who downloaded it has to finish
  the job by hand without being told which half is which.

So the size decision moved to the meter, which already knew how to make it. The
guard plans the tree and charges for **every** convertible file *before any model
runs*; a folder that does not fit the day's budget or the visitor's daily
allowance is refused whole, with its price named. Nothing is ever converted in
part.

```bash
S2P_DAILY_LIMIT=12             # conversions per visitor per day
S2P_DAILY_BUDGET_USD=5.40      # the shared card: 45 conversions a day
```

`S2P_DAILY_LIMIT` is therefore also the largest suite this deployment will
convert on its own card. Unset, it defaults to 10. Bigger repositories are for
the visitor's own API key, which has no ceiling at all.

`--only` still narrows a run, because that is a person choosing which files they
want rather than the tool deciding for them.

All three places that ask "what will this run convert?" read the same manifest,
so they cannot disagree:

| caller | what it does with the answer |
|---|---|
| `guard.py` → `suite.conversions()` | charges the meter |
| `playground._plan_from()` | quotes "costs N of today's conversions" |
| `suite_graph.plan()` | actually converts |

## 4. Using it

```bash
uv run s2p scan samples/selenium-suite
```

```
Suite samples/selenium-suite · 12 source file(s) · 12 to convert, 0 to copy, 0 skipped
wave  file                           kind         lines  needs first
1     pages/AlertsPage.ts            page-object  39     —
1     pages/DynamicLoadingPage.ts    page-object  20     —
1     pages/IframePage.ts            page-object  27     —
1     pages/LoginPage.ts             page-object  30     —
1     pages/UploadPage.ts            page-object  24     —
1     pages/WindowsPage.ts           page-object  29     —
2     tests/alerts.spec.ts           test         46     pages/AlertsPage.ts
2     tests/dynamic-loading.spec.ts  test         28     pages/DynamicLoadingPage.ts
2     tests/iframe.spec.ts           test         28     pages/IframePage.ts
2     tests/login.spec.ts            test         39     pages/LoginPage.ts
2     tests/upload.spec.ts           test         40     pages/UploadPage.ts
2     tests/windows.spec.ts          test         35     pages/WindowsPage.ts
```

The same contract as `s2p convert`: the human reading goes to **stderr**, the
machine-readable artifact to **stdout**. `--json` prints the manifest, `--out
FILE` writes it as well, and the exit code is `0` when there is work to do, `1`
when there is nothing convertible in the folder, `2` for a usage error.

One entry of `s2p scan samples/selenium-suite --json`:

```json
{
  "path": "tests/login.spec.ts",
  "kind": "test",
  "action": "convert",
  "reason": "mocha test file",
  "wave": 2,
  "lines": 39,
  "language": "typescript",
  "automation": "selenium",
  "runner": "mocha",
  "imports": ["pages/LoginPage.ts"],
  "imported_by": [],
  "external_imports": ["selenium-webdriver", "selenium-webdriver/chrome", "chai"]
}
```

`imports` is what step 9.2 waits for. `imported_by` is the reverse arrow, and it
is what step 9.3 uses to explain a failure: if `pages/LoginPage.ts` fails its
gates, the report can say which specs are affected.

There is a second use for `imports` that is worth spelling out. The graph
already accepts companion files as `context_paths` — that is how a single-file
conversion is shown an already-converted page object. In suite mode those
companions are not typed by hand: **a file's `imports` list *is* its context
list**, and by the time its wave runs, every one of them has already been
converted. The dependency graph and the prompt context turn out to be the same
graph.

## 5. Why regex and not a TypeScript parser

`import_specifiers` is one regular expression over the source. The same
reasoning as `classify.py` and the validators applies: import statements are the
most regular lines in the language, and the cost of getting one wrong is bounded
— a missed edge means a file is converted without a companion in its context,
which is a weaker prompt, not broken output. The compiler gate downstream still
has the final say. The day a real suite is demonstrably misread, that is when a
parser earns its place.

Resolution does what TypeScript does: `"./BasePage"` is tried as `BasePage.ts`,
`.tsx`, `.js`, then `BasePage/index.ts`. A specifier that does not start with
`.` is a package (`chai`, `node:os`) and is recorded separately in
`external_imports` — useful later for generating `package.json`.

## 6. The sharp edges

* **`node_modules` must be skipped, or the scan never ends.** `SKIP_DIRS` also
  drops `dist`, `out`, `coverage`, `playwright-report` and `.s2p` — build output
  and our own output are not input.
* **An import cycle has no valid order.** Two page objects importing each other
  cannot both go first. `plan_waves` converts the cycle's members together in
  one wave — the only order they can have — and adds a note saying so: they
  convert, just without the guarantee that each sees the other already
  converted. The first version put every file a cycle *blocked* into one last
  wave too, and on `goenning/typescript-selenium-example` that was twelve of
  thirteen files: its `lib/index.ts` barrel re-exports `lib/page.ts`, which
  imports the barrel back, and every page object and spec imports the barrel.
  Layering over strongly connected components instead gives that repo five
  waves — the decorator helper, the `lib/` cycle, one standalone page, the
  `pages/` cycle, the two specs — and each wave sees the one before it converted.
* **A file that never imports Selenium can still be a Selenium file.** A
  repository that wraps WebDriver in its own `lib/` has page objects that
  import `../lib` and nothing else; read alone, `classify()` calls that "no
  recognised automation library", and in a folder that answer means *copy* —
  so every page object was carried into the converted tree untouched. On that
  same repo 4 of 16 files import `selenium-webdriver` and 13 of 16 reach it.
  `reaches_selenium` re-reads every unplaced file along its imports until
  nothing changes: a file that imports a Selenium file is Selenium, and its
  reason names the path (`page object / helper driving Selenium through
  lib/index.ts`; `via` in the manifest JSON). A recognised library is never
  overridden — a Playwright spec importing the wrapper is still Playwright —
  and a language v1 does not convert is refused exactly as a direct import in
  that language would be. The single-file command still refuses such a page
  object on its own, and rightly: alone, the honest answer is "no library here".
* **A relative import that leaves the folder resolves to nothing.**
  `"../../shared/Base"` from a suite root is outside the suite; it is not an
  in-suite edge and it is not an external package either. It simply is not in
  the plan, and the file is converted without it — a real limitation, and one
  9.3's report should surface.
* **The scan is reproducible on purpose.** Same folder in, byte-identical
  manifest out; everything is sorted, nothing depends on filesystem order. A
  plan you cannot reproduce is not a plan you can put in a report — and it is
  what lets a test assert on the whole payload.

## 7. What this step is *not*

No model is called, no file is written into the suite, and nothing is converted.
`s2p scan` is read-only. Fan-out (`Send`, the per-file subgraph, the reducer
that collects results) is 9.2; the whole-tree `tsc`, the aggregate scorecard and
the consolidated TODO ledger are 9.3.

## 8. Review checklist

1. Why does a **wave** — rather than a flat topological order — give step 9.2
   what it needs?
2. `support/users.ts` gets `supported=False` from `classify()` but action
   `copy` from the scanner. Explain why that is not a contradiction.
3. A file's `imports` list will be used for two different jobs in 9.2. What are
   they?
4. If two page objects import each other, what does the plan say, and why is
   that better than either alternative?
