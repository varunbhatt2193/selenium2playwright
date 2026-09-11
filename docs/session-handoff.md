# Restart here — 2026-09-09 (Phase 11: 11.1a/11.1b/11.2/11.3a done; ⛔ Anthropic API is limit-blocked until 2026-10-01)

## Current position

**Phases 0–10 complete; 11.1a, 11.1b, 11.2 and 11.3a done. 🏁 M4 shipped at 9.3.**

**Two things are live, and it matters which is which:**

| | | |
|---|---|---|
| **<https://varun-s2p.fly.dev>** | Fly app `varun-s2p` | **the page a person opens.** Upload one file or a zip of a folder; get the converted tree back as a zip with the report inside. This is the link to put on a CV. |
| `https://s2p.fly.dev` | Fly app `s2p` | the graph API. JSON only — a browser gets `{"detail":"Not Found"}`. Needs a bearer key. |

`./deploy/fly/deploy.sh` ships the graph; `./deploy/fly/deploy-ui.sh` ships the
page. **Four apps now** (`s2p`, `s2p-postgres`, `s2p-redis`, `varun-s2p`) ≈
**$23/month**, flat and traffic-independent — a public link does not move it.
Model spend is the only cost that responds to traffic, capped at ~$5/day.

### 2026-09-09, second session — 11.3b: the whole repo as evidence, imports decide what is Selenium, waves around cycles

Commits `1536cf0`, `e17fc4d`, `86a8ef4`, `081df5d`, `2d723b9`, `e6667bc`, and one
more for the three follow-up fixes. Deployed: both apps, three times (last at
`e6667bc`; the follow-ups are not deployed). 743 offline tests.

**Varun's decision, restated:** give the model the entire repository. Built
as **context, not output** — one file is still emitted per call, and every
gate, the scorecard, the meter and the repair loop are untouched. The
hosted page is not for whole suites; anyone who wants one clones the repo
and runs it with their own key. Cost of verification was accepted up front.

**What shipped, in the order it was built:**

1. **`suite.reaches_selenium`** — a file that imports a Selenium file is
   Selenium, to a fixed point. `Classification.via` names the path
   (`page object / helper driving Selenium through lib/index.ts`; `via` in
   the manifest JSON). goenning 4 → 13 conversions, imranwijaya 6 → 16,
   webdriverjs-pom 3 → 5, sadabnepal and the sample suite unchanged.
   `docs/gap-log.md` **T15**. Whole-repo context could never have done
   this: it changes what the model *reads*, not which files get a call.
2. **`plan_waves` over strongly connected components** (Tarjan, then Kahn).
   The old planner dumped everything *behind* a cycle into one last wave;
   goenning's `lib/index.ts` ↔ `lib/page.ts` cycle would have put twelve of
   thirteen files in one blind, parallel wave. Now five waves, both cycles
   named in the notes.
3. **Suite evidence** (`suite_graph.repo_evidence`, `graph.read_repo`,
   `prompts.RepoEvidence`): each job also carries `repo_paths` (most
   relevant first: direct callers, converted siblings, pending files, copied
   helpers), `caller_paths`, `pending_paths`; copied files join
   `carried_paths`. `intake` reads them into `repo_files`, cut to
   `S2P_REPO_CONTEXT_BYTES` (128 KB, 32 KB per file; **`0` switches it off
   without a deploy**). Rendered after the companions as `<caller_file>` and
   `<suite_file status="converted|pending|unconverted">`. **No gate ever sees
   one** — `validate` reads `context_files` only — and with nothing sent the
   prompt is byte-identical, so single-file mode and every eval row are
   where they were. `tests/test_repo_context.py` pins both halves. The guard
   refuses the three new path inputs for demo keys.
4. **Deploy landmine, root cause found.** `deploy.sh` pushed *every* key in
   `.env` to the graph app as a secret, and `.env` carries
   `S2P_DAILY_LIMIT=15`. That is why the "unset" secret was back: every
   deploy re-created it. Keys the toml's `[env]` versions are now skipped
   (`86a8ef4`). **The existing secret is still there and still 15** — I did
   not unset it; the toml says 12, the UI reads the cap from the graph's
   `/limits` so the page and the meter agree at 15 in practice, but the
   repo text and the deployment disagree. Decide: `fly secrets unset -a s2p
   S2P_DAILY_LIMIT` (then goenning, 13 files, is refused on the hosted
   page) or set the toml to 15. Also: the busy check died silently under
   `set -e` when its query failed — one deploy exited 1 with an empty log —
   fixed in `081df5d`.

**Live run 1 — sadabnepal, evidence as first worded, s2p on `e17fc4d`:**
3 passed · 4 needs-review · every gate green on every file · tree compiles
(0 findings, 1 excused) · 178.6 s. Baseline v25: 5 passed · 2 needs-review ·
122.5 s. The two files that slipped from passed to needs-review both said
why in their TODOs: *"Kept string extractor temporarily for pending caller
compatibility"* — `login.page.ts` kept `getHeaderText()` returning a string
so the still-Selenium spec would keep working. **The callers section had
taught the actor to stay compatible with Selenium.** That is the risk
named in the plan, measured on the first run. Fixed in `2d723b9`: callers
are "rewritten later in this run against the file you produce — not an API
to stay compatible with", full Playwright idioms, no transitional members
or TODOs whose only reason is an unconverted caller. (The stream from this
laptop also closed after 0.2 s while the server ran the suite to the end;
the results above were read back from LangSmith. The verification script
now creates the run and polls, and logs the visitor id, because a demo
thread is only readable under the visitor that made it.)

**Live run 2 — sadabnepal, reworded prompt, s2p on `2d723b9`:** **5 passed ·
2 needs-review · tree compiles · 164.0 s**, no shims, `unexplained: 1`
(`browserConfig.ts` dropped `getBrowserInstance` with no reason — the same
file that regressed on 2026-09-09 morning; not new). Back to the baseline's
shape with the whole repo in view. Every page object converted in 2 laps,
both specs in 1.

**Live run 3 — goenning, first attempt, s2p on `2d723b9`: invalid.** The
planner dispatched 13 files and the single-file graph **refused nine** at
intake — `classify()` reads one file alone, which is the very limit the
scanner exists to get past. Fixed in `e6667bc`: the job carries `via`, and
intake re-reads its verdict with `classify.through`. 13 conversions spent.

**Live run 4 — goenning, s2p on `e6667bc`: 13 of 13 converted, 0 passed ·
13 needs-review · tree does not compile (11 findings) · 419.4 s.** Every
file took 3 laps with the critic on revise. Read out of LangSmith, three
causes, all fixed offline in the commit after `e6667bc` and **none verified
live yet** — the meter had 2 conversions left and the run costs 13:

1. **`import … from '../../out/lib'`.** The specs were shown their source
   under `/…/src/` and their converted companions under `/…/out/`, did the
   arithmetic, and wrote a path that can never compile. `prompts.shown`
   now labels every file by its path inside the suite (`suite_roots` on
   the job) and says imports follow the original's specifiers.
2. **T14, one layer out.** The `lib/` cycle (5 files) converted blind, three
   with type errors of their own; every spec importing the barrel then
   failed compile for nine findings in files it could not edit, and the
   critic asked the spec to *"restore green compilation for the provided
   converted companion files"* three laps running. `compile_check` now
   excuses an error inside **any** companion (`others=`), converted or not;
   what a companion breaks *in this file* still blocks; the whole-tree
   compile still counts everything. `tests/test_graph_validation.py` had a
   test asserting the old behaviour; it is flipped, with the reason.
3. **The critic argued with the callers.** `lib/components.ts`: every gate
   green every lap; the critic asked for `getText()` to go (rule 28), then
   to come back ("callers rely on it"), then to go again. The reviewer now
   gets `review_context` — companions, no suite evidence. The writer keeps
   the evidence.

**What is left after those three, and it is structural:** an import cycle
converts blind by definition — five `lib/` files each invented their own
`Browser`/`Page` contract, and `lib/index.ts` re-exported a `WaitCondition`
from two of them. The evidence shows cycle-mates each other's *Selenium*,
not each other's conversions. The fix worth designing is a **settle pass**:
after a cycle's wave, convert each member once more with its mates'
converted files as companions. On goenning that is +9 conversions per run.
Decision for Varun; not built.

**Evening of 2026-09-10 PDT (2026-09-11 UTC) — what happened after the commits, in order:**

- The three follow-up fixes (`0c806df`) **are deployed** to both apps. The
  goenning verification run **was not made**: the chain that would have
  run it was stopped, see next point. Both apps run `c72b18a`'s code.
- **My deploy restarted the graph while Varun's run was in flight** → the
  page showed *"The backend failed (500)"*, and the server re-claimed the
  orphaned run. His retry started 12 s after the restart, and **both runs
  wedged with the CPU idle** (load 0.04, no model call for 3 min, the
  suite node never advancing to wave 2). The wedge began the second two
  suites ran concurrently in one worker — the same shape as the three
  wedges of 2026-09-08 that were blamed on CPU credits. **Working
  hypothesis: two concurrent suite runs deadlock the sync fan-out.** Not
  proven; do not run two suites at once, and never deploy while one runs.
  Remedy that worked: `UPDATE run SET status='interrupted'` for every
  running row, then `fly machine restart`, then confirm 0 in flight.
- **Two Fly secrets were set on `s2p` for the demo video, at Varun's
  request, and should be put back afterwards:**
  `S2P_DAILY_LIMIT=45` (per-visitor cap; was 15 via the same secret, toml
  says 12) and `S2P_DAILY_BUDGET_USD=12` (100 conversions/day; toml says
  5.40 = 45). The page reads both from the graph's `/limits`, so no UI
  change was needed. Put back with
  `fly secrets unset -a s2p S2P_DAILY_LIMIT S2P_DAILY_BUDGET_USD` — this
  restarts the machine, so check nothing is in flight first. `deploy.sh`
  no longer pushes either from `.env`.
- **The goal for the evening was a demo video.** The repo to film is
  `sadabnepal/selenium-javascript-test` (7 conversions, 3 waves, ~3 min,
  comes back 5 passed · 2 needs-review · tree compiles). A run of it was
  in progress at handoff time. The video goes into `DEMO_VIDEO` in
  `ui/web/src/components/Home.tsx` (a YouTube/Loom share link or an `.mp4`
  URL), then `cd ui/web && npm run build` and `./deploy/fly/deploy-ui.sh`
  — the UI deploy restarts only the page, but do it between runs.
  `imranwijaya/selenium-typescript-example` planned as 16 conversions in
  7 waves on the page and was refused at the old cap of 15, which is why
  the cap was raised.

**Budget used on UTC 2026-09-10: 43 of 45** — guardrails probes 3,
sadabnepal 7 + 7, goenning 13 + 13. **Next:** deploy both apps (the three
fixes are committed, not deployed), run goenning once more — with the
owner key from the environment if the meter has not reset, `--owner` in
`live_suite.py` under the session scratchpad — and read the same four
numbers. Expected if the fixes hold: both specs green, the `lib/` cycle
still red until a settle pass exists.

### 2026-09-09 session — commits `16574f8`, `1c86ab4`, `7f9d614`, `598980c`, `b442cdc`

Deployed: graph app **s2p v25**. UI unchanged (nothing in this session touches it).

**Rules Varun set this session, both binding:**

- **No partial repo conversion, ever.** Either one file with the site's key, or
  the whole suite. A suite that does not fit the budget is **refused**, never
  trimmed. The demo-cap machinery (`CAP_ENV`, `demo_caps()`, `apply_caps()`,
  `cap_note()`, `_tally()`) is **deleted**, not disabled. The single-file cap
  stays: 45 conversions/day globally.
- **BYOK means cloning the repo.** There is no way to pass a key per call and
  there must not be — *"no one will trust this website with their key."* An
  in-progress web-BYOK feature was reverted to a zero diff; the only surviving
  trace is a paragraph in `llm.make_model`'s docstring saying why.

**Landmine:** a Fly **secret** silently outranks `[env]` in the toml. A stale
`S2P_DAILY_LIMIT=15` secret shadowed the versioned `12` for an unknown period.
It is unset now, and both tomls carry a comment saying so. Check
`fly secrets list -a s2p` before trusting any `[env]` value.

**Also fixed:** the residue gate judged by the *receiver's name* — `\bdriver\.`
— so a correctly converted Playwright helper whose parameter kept the name
`driver` failed the gate and burned a repair lap. It now matches the **member**
reached for, and catches the unambiguous ones (`.quit(`, `.getCurrentUrl(`, …)
on *any* receiver, which is strictly more than before. `close` is deliberately
absent: Browser, BrowserContext and Page all have one. `tests/test_residue_gate.py`
pins both halves. Live: `driverFactory.ts` went 3 laps/revise → **1 lap/pass**.

#### T14 — a gate that fails for the wrong reason destroys the whole attempt budget

`goenning/typescript-selenium-example` scored **0 of 4 on every lap**. Nothing
was wrong with the conversions. Two layers, same defect, both now fixed:

1. **The compile gate** excused by *error code* only (absent package). Compile
   is the one gate that must be handed the companions — `tsc` cannot resolve an
   import without the file behind it — so it is the one gate that needs telling
   which of them were never converted. `compile_check(files, carried=…)` now
   excuses by **location** too; `validate()` derives the set from
   `carried_paths`, which already existed for the prompt.
2. **`split_tree_findings`** decided ownership by *classification* — "does this
   carried file still look like Selenium". goenning wraps WebDriver in its own
   `lib/`, so all twelve carried files classify `automation=unknown`, matched
   nothing, and fell through to `companion`, which counts. Now keyed on an
   **import edge**: a carried file that imports something this run converted can
   have been broken by it; one that does not, cannot. Direct, not transitive, on
   purpose — a barrel re-exporting a converted module would make the whole repo
   a caller again, which is the bug being replaced.

Also: `tsc` reports `Cannot find name 'describe'` as **TS2582**, not TS2304,
whenever it can name the `@types` package. Same error, now the same answer.

**What made this a taxonomy entry** (`docs/gap-log.md` T14) rather than a bug
report is the critic's behaviour. It diagnosed the problem perfectly and was
helpless — *"the reported compile findings are all in explicitly unconverted
companion Selenium files… restore/rerun the validation with the intended target
scope"* — which is the one repair a reviewer cannot perform. It voted revise
because a gate had failed, and spent every attempt on all four files. **A gate
that fails for the wrong reason does not merely mis-score; it converts the
entire attempt budget into nothing, and the more honest the critic is, the more
completely it stalls.**

Measured live, same repo, s2p v25: tree errors blamed on the conversion
**49 → 1**, correctly attributed to carried files **0 → 43**, compile gate from
failing all four files to passing three. The one remaining is real and the gate
is right to fail it: `lib/ensure.ts:10 TS18047 'text' is possibly 'null'` —
`textContent()` returns `string | null`.

#### The root cause underneath all of it, and the next thing to fix

**`classify()` is per-file text matching for `selenium-webdriver` imports.** A
repository that wraps WebDriver in its own abstraction layer has page objects
that import `lib/index`, never Selenium — so they read as "no recognised
automation library", get **copied**, and sit in the output tree as Selenium
forever. Everything above was making the tool honest about a tree that should
never have had twelve Selenium files in it.

Measured on goenning: **4 of 16** files are directly Selenium; **13 of 16**
reach Selenium transitively through imports. Nine would flip from `copy` to
`convert` — every page object, `specs/spec.ts`, and the `lib/index.ts` barrel
that caused the whole mess. Script: `scratchpad/transitive.py`.

**Varun's decision: give the model the entire repo**, so this class of problem
cannot arise. My recommendation was classification-first (bounded, testable,
cheaper), and it was heard and overruled — build the whole-repo path. The
honest trade-offs to carry forward:

- Whole-repo **context** is cheap and strictly more information. Whole-repo
  **output** is where the risk is: structured output for an entire tree is T9
  territory, per-file attribution and the scorecard disappear, the repair loop
  rewrites everything each lap (so a `browserConfig`-style regression happens
  repo-wide, not in one file), and per-file metering stops working.
- The suite architecture already delivers most of what whole-repo promises. T13
  says a single-file conversion cannot guess its callers — but converted in
  waves, callers get the *converted* page object as their companion, and the
  Phase 9.3 run did twelve files with **zero interface mismatches**.
- So the shape worth building first: **separate context from output** — give the
  model the whole repo as read-only evidence, keep emitting one file per call.

#### Two live runs worth keeping

`sadabnepal/selenium-javascript-test`, s2p v23: **5 passed · 2 needs-review ·
tree compiles · 122.5s**, `unexplained: 0`, fixture `login.json` present in the
output. The two needs-review are honest — `driverFactory.ts` passes all four
gates *and* the critic and is flagged only for its `TODO(review)` items, which
is the system working as designed.

`browserConfig.ts` is the one file that failed for a reason worth fixing, and it
is **not** a gate defect. The critic raised a **different objection each lap**:
lap 1 an invented `remoteServerURL` field and a silently changed export
contract; lap 2 — after that was fixed — dropped Firefox `dom.webnotifications`
/ `dom.push` prefs from the non-docker branch, a *new* regression introduced by
the repair. Lap 3 ran out. **The regression shipped**: `getFirefoxConfig()`
returns bare `{ browserName: 'firefox' }` locally while the docker branch keeps
`firefoxUserPrefs`. Three attempts is one budget shared between fixing findings
and absorbing new ones, and the critic re-reviews from scratch with no memory of
what it already accepted.

### Four layers under one symptom (2026-09-08 → 09), commits `a376904`..`a2e61f5`

Every layer was found by reading data, not by reasoning about the code, and each
one was hiding the next. The symptom the whole way down was the same: **files
come back `needs-review` and the tree does not compile.**

1. **The tree verdict blamed the run for its input.** 43 of 43 errors were in
   files the run never converted. `split_tree_findings` sorts them four ways —
   `converted` / `companion` / `unconverted` / `dependency` — and only the first
   two go red. `owned_findings()` **fails closed**: a missing split means every
   error is ours. (An existing test caught me shipping the fail-open version.)
2. **Path aliases were invisible to the compile gate.** It wrote a `tsconfig`
   with no `paths`, so `@lib/x` could not resolve. `alias_paths()` derives them
   from the tree. On the real repo: **101 errors → 32**.
3. **Path aliases were invisible to the import graph too.** `resolve_import`
   returned `None` for anything not starting with `.`, so the wave plan and the
   companion list were empty and every file compiled alone. Fixed: zero edges →
   14 files with real ones, per-file compile **0/3 → 3/3**.
4. **The gates were judging the neighbours.** `residue_check(files)` saw
   unconverted Selenium companions and failed clean Playwright output. Residue
   and lint are now scoped to the converted file; compile still gets the
   companions, because `tsc` needs them. All four gates then passed 3/3.

**Then the critic was still voting revise on all three.** Its own words, pulled
from LangSmith: *"the compile report is marked PASS compile: passed but lists
TypeScript errors in companion files."* Two lies in the evidence, both fixed in
`a2e61f5`:

- `ValidationReport.excused` — errors the gate saw but did not count (absent
  package, another file's module) no longer render under a `PASS`. `render()`
  is the critic's to-do list and the repair prompt's; an unfixable item there
  reads as work. The report, the CLI and `split_tree_findings` still show all of
  them.
- `carried_paths` — past the demo's cap a suite copies files across untouched,
  and those raw Selenium files were handed to the model under *"These companion
  files are ALREADY converted to Playwright."* They now arrive as
  `<unconverted_file>`, labelled not-the-target-API and not-yours-to-fix. They
  stay in the compile set regardless.

Measured on prod, same repo, same three files: **0/3 passed, 3+3+3 laps → 1/3
passed, 1+1+3 laps, tree compiles, `tree_findings_mine: 0`, 105s → 88s.** The
two remaining `needs-review` are honest: one leaves a TODO about an unconverted
companion, one is a `WebDriver`-returning factory with a real API break.

Also fixed: `gates_line` is a property and `asdict` copies fields, so the final
`done` payload reached the page without it and the **GATES column went blank the
moment a run finished**. The live `file` events had always sent it.

### The demo cap (2026-09-08, commit `a376904`)

`S2P_SUITE_MAX_TESTS=3` / `S2P_SUITE_MAX_PAGE_OBJECTS=3`, set on **both** Fly
apps (they must agree — the UI plans, the graph converts). Applied in
`scan_sources` so guard, playground and graph give the same answer. A file past
the cap is switched from `convert` to `copy` and carried across; a test whose
page object did not fit is dropped with it, because converting one without its
companion compiles it against an API that is no longer there. The note on the
page says so and invites cloning the repo with your own key.

**Known design flaw, not yet fixed.** The cap takes the first wave, and the
first wave is always the *leaves* — `webdriver.factory.ts`, `lib/browser.ts`,
`DriverSetup.ts`. Those are the hardest files in any suite and the least
interesting to a reader. On `imranwijaya` it converted **zero test files**. A
visitor wants to see a spec become a Playwright spec. Selecting a representative
slice (one test plus the page objects it needs) would make every repo demo
better; see "Try-it-out repos" below.

### Try-it-out repos — screened 2026-09-09, none yet perfect

Eight repos screened with the **free** `/api/suite/plan` endpoint (costs no
conversions). Best three, and what actually happened:

| repo | licence | verified |
|---|---|---|
| `sadabnepal/selenium-javascript-test` | ISC (package.json) | ran live: **2 passed, 3 needs-review**, tree does not compile |
| `biswajitsundara/selenium-mocha-typescript-starter` | ISC (package.json) | plan only — 3 waves, whole suite converts |
| `imranwijaya/selenium-typescript-example` | MIT | ran live: 1 passed, 2 needs-review, **0 test files** |

Ruled out: `goenning` (decorator DSL — the hard case), `cdap-ui` (too large),
`MochaTypescriptTest-101` (one file), `EzequielCaballero` (no tests converted).

**Three fixes stand between these and a green demo. None needs model spend to
find, only to verify:**

1. **Bare `baseUrl` imports are not resolved.** `sadabnepal` writes
   `import { LoginPage } from 'tests/pages/login.page'`. `alias_paths` and
   `resolve_import` handle `@x/*` and `~/*` but not un-prefixed baseUrl paths,
   so 8 imports went unresolved and `e2e.spec.ts` came back 3/4 gates. Same bug
   as the alias one, one step further out.
2. **`resolveJsonModule` is off** in `sandbox/tsconfig.base.json`. Any suite
   importing test data (`login.json`) fails the tree compile. It was the *only*
   error attributed to the conversion in that entire run. One line.
3. **Cap selection** — see the flaw above.

### Working rules learned the hard way (2026-09-08)

- **Never deploy while a run is in flight.** A machine restart makes
  `sweep_abandoned` re-claim in-flight runs; my deploy stranded two of Varun's
  suites. `deploy/fly/deploy.sh` now has `refuse_if_busy()` (heredoc piped to
  `fly ssh console -C "python -"`; `S2P_FORCE_DEPLOY=1` overrides). The first
  version of that guard returned empty through nested quoting, which the script
  read as "carry on" — a guard that could not fire. Check a guard can fail.
- **Deploy both apps or neither.** Shipping the graph without the UI left the
  plan endpoint serving stale code that disagreed with the backend.
- **Cancel a wedged run through Postgres,** not the SDK: `runs.cancel` timed out;
  `UPDATE run SET status='interrupted' WHERE status='running'` worked.
- **I predicted "this will go green" three times and was wrong three times,**
  costing 9 conversions. Verify with a live run before saying it works.
- SSE needs a **15s heartbeat** (`ui/server.py`), or a proxy closes a quiet
  connection and the browser shows "network error" while both servers log 200.
  A wave of parallel files went silent for 95.1s on a live run.

### What 11.3a added (2026-09-08)

**A suite can be uploaded.** `source_tree` (relative path → contents) carries a
folder as text, so nothing in the request names a path on the server — which is
the only property `guard.py` was ever protecting. `plan` materializes it into a
temp directory *it* chooses; `finish` reads the tree back out as text and
deletes the workspace; every node between is 9.2/9.3 unchanged. `root` and
`out_root` stay refused for anyone but the owner, so naming a folder **by path**
is still local-only (`folder_blocker()` hides that input).

Three things that had to hold, and would be expensive to rediscover:

- `suite.safe_path` is a whitelist of shapes, not a blacklist of tricks, and a
  tree with one bad key is refused **whole** — a suite that silently dropped a
  file would convert, compile, and be wrong in a way nobody looks for.
- `sweep_workspaces()` runs at the start of each suite and deletes workspaces
  older than an hour, because `finish` cannot clean up after a run that never
  reached it.
- `limits.spend(runs=n)` charges **per conversion** (INCRBY, atomic). The meter
  counted runs, and one twelve-file suite is twelve conversions. Copied helpers
  are free: the guard prices a tree with `suite.conversions`, the same call the
  page uses for the number on screen. Commit `084af3e` (2026-09-08), **deployed
  to both Fly apps the same day**, so the live demo charges for conversions and
  a 16-file repo with 4 Selenium files in it goes through.

**Playbook rule 28.** A wait fused to a getter is not behaviour to preserve —
web-first assertions retry, so `expect(locator).toHaveText(/\S/)` is the wait
and the judgement in one line. The sample suite went **8/12 with a tree that
would not compile → 12/12 with the tree compiling, 51.3s → 20.4s, every file on
its first attempt.** Deliberately not a `tsconfig` change: adding `DOM` to `lib`
would cost the compile gate its ability to catch browser APIs hallucinated into
Node context. Gated on three hard-suite files (the 11.1b `document` failure is
gone, nothing regressed) — **not** a full eval run, which is worth finishing if
the rule ever becomes load-bearing in a published number.

**Transitive compile context.** `dispatch` handed each file its *direct* imports
only, so on a suite three or more deep a spec's page object could not resolve
its own BasePage. Measured with the real gate and no model: three phantom
findings before, zero after — and the repair loop was spending its three
attempts on them. Latent since 9.2, invisible because `samples/selenium-suite`
is exactly two deep **and** because the 9.2 test asserted the buggy answer.

**Sharp edges found by running it:**

- `suite_result` named both the converted files and the whole-tree compile
  report `tree`, so the download button silently offered a zip containing
  `{"gate": "compile", "passed": true}`.
- `suite_key()` returned the **owner** key on every call. That would have worked
  perfectly against `s2p.fly.dev` and bypassed the meter completely, because
  `guard_run` returns `True` for an owner *before* `limits.spend` is reached. It
  is now scoped to local backends and pinned by a test named after exactly that.
- `LANGGRAPH_DEPLOYMENT_URL = "http://s2p.internal:8000"` is the obvious thing to
  write and does not work. Fly's private network is IPv6-only and the LangGraph
  image binds `0.0.0.0`. Measured from inside the UI machine; the finding is in
  `deploy/fly/ui.toml` beside the line it explains.
- `fly apps create` **creates** — there is no dry-run. Using it to check name
  availability made three apps; two were destroyed.

`S2P_DAILY_LIMIT` is now **15** (was 10) so one 12-file suite fits per visitor
per day; the global budget is unchanged at 41 runs/day ≈ $5.

**Next in 11.3:** the codemod comparison table (`plan-review.md` item 5 — one
README table, *with an honest row where a codemod wins*), ADRs (item 15, first
one "why StateGraph, not `create_agent`"; there is no `docs/adr/` yet and the
reasoning is scattered through `plan.md` and the walkthroughs), public trace
links, the suite demo video, LinkedIn assets.

### Before 11.3a

A file sent as text comes back `compile=PASS residue=PASS lint=PASS
parity=PASS`, critic pass, with the reflection loop taking real laps. Three
machines in `iad` (this image, `pgvector/pgvector:pg16`, `redis:6`), about
$19.50/month. `./deploy/fly/deploy.sh` deploys and redeploys;
`./deploy/fly/cost.sh` shows what is running; `./deploy/fly/teardown.sh --yes`
is the off switch — Fly has no spending cap, so that script *is* the cap. Read
[deploy/fly/README.md](../deploy/fly/README.md) before touching any of it.

**10.4 is done too: the URL is no longer open.** `POST /threads` with no token
is 401. Two keys in `.env` (`S2P_API_KEY` = you, unlimited; `S2P_DEMO_KEY` =
metered, inline text only, no server paths, no shared memory), a $5/day budget
enforced in runs, 3/60s and 10/day per visitor, an alert at 80%, and a 👎 that
queues its input into the `s2p-feedback-queue` dataset. Verify any deployment
with:

```bash
uv run python scripts/check_guardrails.py --url https://s2p.fly.dev
```

Read [docs/guardrails.md](guardrails.md) before changing `guard.py` or
`limits.py` — it lists four bugs that only running it could find, including a
rate limiter that refused *everything* and looked exactly like one that worked.

## Open threads (decisions waiting on Varun, 2026-09-07)

**Set an Anthropic workspace spend limit.** Not done yet, and it is the only
*hard* cap on model spend that exists — our $5/day budget is a traffic governor
enforced by our own Redis counter, and the owner key bypasses it entirely, so
CLI runs, evals and suite runs are not counted. `.env` already has
`ANTHROPIC_WORKSPACE_ID`, so a limit set at Console → Settings → Workspaces →
Limits caps *this project* and nothing else. Suggested $30–50/month.

**OpenAI free daily tokens — checked, and worth $0 as configured.** The
data-sharing programme covers chat/reasoning models (gpt-5, gpt-4.1, gpt-4o, o1,
o3 and the mini variants); embeddings are **not** on the list, and this project
uses OpenAI only for `text-embedding-3-small`. Varun will confirm on his own
dashboard (platform.openai.com/usage/chat-completions, group by "service tier",
look for "data sharing incentive tier"). The opportunity, if he wants it: point
`S2P_MODEL`/`S2P_CRITIC_MODEL` at `openai:gpt-4.1-mini` for the demo and the
public model spend goes to roughly zero — ~12,500 tokens per conversion against
a 2.5M–10M/day allowance is 200–800 conversions/day free, far above our 41/day
cap. **Do not switch blind.** The Phase 6 shootout only measured Claude models,
and its one weak-model data point is a warning: Haiku at 1 attempt fully passed
2/12 where Sonnet passed 11/12 (reflection rescued it to 9/12). Run
`scripts/run_reflection_ab.py` with the OpenAI mini model as actor first and
decide against the same table as everything else.

**Budget tuning.** `S2P_COST_PER_RUN` is 0.12, a deliberate 2–4× overestimate
(measured: $0.024–$0.058/file). Once there is a week of real traffic, replace it
with the measured median from LangSmith so the dollar figure means what it says.

**CodeQL and Dependabot are now published** (`.github/workflows/codeql.yml`,
`.github/dependabot.yml`, `docs/github-security.md` — Varun's own files, held
back from the 10.2/10.4 commits and committed on 2026-09-07). Publishing them is
only step 1: the repository *settings* that switch on alerts, secret protection
and push protection can be enabled by the owner alone, in
Settings → Advanced Security. The checklist is
[github-security.md](github-security.md); the first CodeQL run should appear
under Actions on the next push.

**10.3 is done: the playground exists — and since 2026-09-08 it is a React page.**

```bash
cd ui/web && npm ci && npm run build                 # the page
uv run --group ui uvicorn ui.server:app --port 8501  # serves it + /api, talks to s2p.fly.dev out of .env
uv run --group ui streamlit run ui/app.py            # the ORIGINAL page; still runs, local-only folder paths
```

`ui/server.py` is seven FastAPI routes (`tests/test_web.py`, 19 tests) over the
same `playground.py`; progress streams as server-sent events. One theme (teal),
no switcher — decided by Varun. Redeploy with `./deploy/fly/deploy-ui.sh`, which
now builds the page in a Node stage of `Dockerfile.ui`. The Streamlit notes
below are still the right mental model for what the page may and may not send.

`ui/app.py` is layout only; every decision is in `playground.py` with 57 tests,
because Streamlit re-runs the whole script on each click and importing the app
file *is* running it. It calls the deployment with `S2P_DEMO_KEY` (never the
owner key — that bypasses the meter) and an `X-S2P-Visitor` header per browser
session, reads `GET /limits` for the budget line and posts to `/feedback` for
👍/👎. Read [playground.md](playground.md) before touching it; the three bugs
only a live run could find are listed there, and the first one matters for any
demo you give: **a spec pasted alone cannot compile**, because it imports a page
object the server has never seen — send the already-converted companion in the
companion box (the `login.spec.ts` sample button pre-fills it).

**Decided 2026-09-07: the launch kit must include a SUITE demo video.** The
playground converts one file at a time and will keep doing so until
suite-in-the-browser is built — the suite graph's inputs are *server
directories* (`root`, `out_root`), `guard.FORBIDDEN_INPUTS` refuses both for
demo keys, and `limits.spend` counts run creations rather than files, so a
12-file suite would tick the budget once while spending twelve conversions. So
a screen recording of

```bash
uv run s2p suite ./samples/selenium-suite --out out/demo --parallel 4
```

— wave plan, parallel conversions, the table, the whole-tree compile, then
`conversion-report.md` — is the only thing that answers "does this scale past
one file?" for someone who cannot run the CLI. Pair it with
[phase-9.3-report.md](phase-9.3-report.md), the real artifact it produces.
Tracked in roadmap 11.3; written here too because roadmap.md is gitignored.

**Phase 10 is complete. Phase 11 has started.** 11.1 was split into a and b on
2026-09-08, because building the ruler and using it are different kinds of work:
**11.1a is DONE** (no model spend, browser verification) and **11.1b is DONE**
(on OpenAI, see the blocker below). **11.2 is DONE too** — execution evals and
the CI gate, also with no model spend; read
[phase-11.2-report.md](phase-11.2-report.md). Next is 11.3
launch kit (drop the 🚧 banner, comparison table, cost numbers, **the suite demo
video above**, LinkedIn assets). 11.3 is also where *hosting the playground
itself* belongs: today the page runs on your laptop against the live backend,
which is enough to demo and not enough to put in a CV link.

### 11.2 is done: the code is executed, and CI checks the ruler

Read [phase-11.2-report.md](phase-11.2-report.md). The four gates read the
converted file and none of them can say whether it *works*. Now the saved
conversions go into a browser against the demo app in a container:

```bash
docker compose -f deploy/the-internet/compose.yml up -d
uv run python scripts/run_execution_eval.py --goldens --suite base     # the gate
uv run python scripts/run_execution_eval.py --experiment out/11.1b/arm-c-playbook2
uv run python scripts/run_execution_eval.py --tree out/9.3 --suite base
```

**No model is ever called here.** A finished experiment already contains the
code it produced, so the execution number costs browser time and nothing else —
which is why this step happened at all while the Anthropic account is blocked.

Three things to carry forward:

1. **Test rows 20/20; every page-object execution failure is a name.** Two runs
   with byte-identical configuration wrote `flash` and `flashMessage` for the
   same locator, and the golden caller says `flash`. Neither conversion is
   wrong. Do **not** answer this with a playbook rule naming the golden's nouns
   — that is fitting the prompt to a fixture. It is hard case 10's wall
   (*"call sites were not provided"*) from the other side, and it belongs to
   suite mode. Gap T13.
2. **One real defect, found twice:** an explicit 10 000 ms wait dropped to
   Playwright's 5 000 ms default (gap T12). Candidate rule, deliberately NOT
   added — the next eval-gated tuning pass should measure it on rows that were
   not used to find it.
3. **The pinned app is not production.** `gprestes/the-internet` is pinned by
   digest and is a 2020 build; one golden assertion depends on a spelling
   upstream fixed later. The golden cannot be edited — its hash is inside the
   published dataset — so the divergence is declared in
   `execution.KNOWN_APP_DIVERGENCES`, excluded from pass/fail, and **the gate
   fails if it ever starts passing**. Never "fix" that by editing a fixture.

`.github/workflows/ci.yml` runs the offline suite and both browser gates on
every push, with **no secrets**, so a pull request cannot spend money. The
offline suite now passes on a machine with no credentials at all, and CI is
**green** (run 34179741487).

The first CI run was red, and its two causes are worth remembering because they
will come back: rich treats GitHub Actions as a colour-capable terminal, and
Typer's usage-error panel is a *different* console from the `cli.console` that
step 9.1 pinned — so assert on that panel through `tests/console_env.py`'s pin
(and `unwrapped()`), never on raw stderr. And never assert a wall-clock constant
on a shared runner: compare something measured inside the same run.

### 11.1a is done: the twelve hard cases are a second benchmark

Read [hard-cases.md](hard-cases.md) first. In one paragraph: the Phase 6.1 set
measures ordinary page objects and tests and the converter passes it, which is
not the same as being good at this job. `plan-review.md` listed twelve patterns
where a mechanical translation compiles, passes lint, passes residue, and
silently tests something else. Those are now fixtures.

```
samples/selenium-hard-suite/       11 Selenium files (the inputs)
samples/playwright-hard-golden/    11 Playwright goldens (the answers)
cd samples && npm run test:hard && npm run test:hard-golden
uv run python scripts/measure_hard_fixtures.py     # re-measure; rewrites the evidence file
uv run python scripts/upload_hard_dataset.py       # local preview, no network
```

**Uploaded and verified: `selenium2playwright-hard-v1-b233d4c101fd`, 11
examples** (receipt: `docs/phase-11.1-receipt.json`). Measured 2026-09-08:
Selenium 7/7 in headless Chrome (21.1s), Playwright goldens 7/7, 0 flaky
(14.7s), all four gates green over the whole golden tree.

Three things a future session must not undo:

1. **Hard cases 2 (dialogs) and 5 (windows) are covered by the Phase 6.1 set**
   and cross-referenced in `COVERED_BY_BASE_DATASET`, not duplicated.
   `check_manifest` fails the build if a case is claimed by both benchmarks or
   by neither, so "twelve" is enforced. Do not "fix" the coverage gap by
   writing a second alerts page.
2. **`tests/shared-session.spec.ts`'s golden carries a `TODO(review)` on
   purpose** (`storageState` is a suite-level fix a single-file conversion
   cannot make). `plan-review.md` finding 6: a dataset whose references never
   admit a limitation teaches the agent to guess confidently. That TODO is the
   feature.
3. **`snapshot_example` gained `source_dir`/`golden_dir` and
   `upload_collection` gained `description`** — both additive, both defaulting
   to the Phase 6.1 values. `BaseDatasetUnchangedTests` asserts the published
   set still fingerprints to `selenium2playwright-v1-4920b5f319d8`. If that
   test ever fails, the 6.1 dataset has silently moved.

**Honest gap, written down so nobody claims otherwise:** real Selenium v3
promise-manager code cannot be browser-verified under selenium-webdriver v4, so
hard case 12 uses returned promise chains (no `await` keyword in the file, same
conversion task). Implicit v3 ordering is not covered. Under hard case 8 only
hover is exercised — drag-and-drop and modifier clicks are still open.

### ⛔ Still true: the Anthropic account is limit-blocked until 2026-10-01

Every Anthropic model returns `400 invalid_request_error: You have reached your
specified API usage limits. You will regain access on 2026-10-01 at 00:00 UTC.`
Account-wide, not model-specific — a one-token `claude-sonnet-5` probe gives the
same error. Fix it at Console → Settings (very likely the spend cap advised after
10.4), or wait for 2026-10-01.

#### 🔁 The deployment now runs on OpenAI — flip it back when Anthropic returns

The outage took `s2p.fly.dev` down with it, so on 2026-09-08 the live app was
switched to OpenAI. This is the model-agnostic rule ([[model-agnostic-rule]])
paying for itself: no redeploy, no image rebuild, **one secret**.

```bash
fly secrets set -a s2p S2P_MODEL=openai:gpt-5.4      # what was done
fly secrets unset -a s2p S2P_MODEL                   # back to the code default (Sonnet)
```

`OPENAI_API_KEY` was already a Fly secret — `deploy.sh` forwards every key in
`.env`, and recall's embeddings use OpenAI. There was no `S2P_MODEL` secret at
all, which is why the app had been falling back to the code default
`anthropic:claude-sonnet-5`. Setting it triggers a rolling restart; the critic
follows the actor automatically.

**Verified live 2026-09-08:** `HoversPage.ts` came back **4/4 gates + critic
pass, no TODOs**, `models: {'actor': 'openai:gpt-5.4', 'critic':
'openai:gpt-5.4'}`. `/ok` is 200 and `/limits` reports the full 41 runs
remaining. One `NotFoundError` on the first call immediately after the restart,
clean on the retry — treat a 404 in the first seconds after a secret flip as the
machine still coming up, not a bug.

**Two consequences to remember:** demo conversions now bill the OpenAI account
(the $5/day guard still applies — it counts *runs*, not provider), and the
playground's "models" line shows `openai:gpt-5.4`, so anyone watching can see it
is not Claude. Flip it back when Anthropic access returns, because the README's
headline numbers are Claude's.

The failed Opus baseline spent zero tokens. Its uploaded experiment
`s2p-11.1b-claude-opus-5-attempts3-f6b74c60` is an **outage record, never a
score** — do not compare anything against it.

### 11.1b is done — on OpenAI, because Varun chose not to wait

Read [phase-11.1b-report.md](phase-11.1b-report.md). Ran on `openai:gpt-5.4`
(actor + critic), so **its numbers are not comparable to the Opus/Sonnet figures
in phase-6.4 or reflection-shootout**. Four runs, **$1.57**, all cloud-verified.
**Graph-passed 6/11 → 9/11** across two eval-gated playbook edits.

**The finding that matters more than the scores:** two *identical* baseline runs
gave 9/11 vs 11/11 gates and 7/11 vs 6/11 graph-passed, with four of eleven rows
changing outcome. Temperature is unset; these models are not deterministic. So
an 11-row, one-run-per-arm A/B **cannot** attribute a one- or two-row delta to a
prompt edit — the argument has to rest on reading the generated code. Any future
session tempted to publish "the playbook improved X%" from a single pair of runs
should read that section first.

What was stable was the *diagnosis*: three page objects never reached `passed`
in either baseline, and failed the same way both times.

**New playbook rules, both measured:** 26 (`executeScript` is a workaround until
proven otherwise — delete the JS click and the scroll) and 27 (frames are scoped,
not entered — `enterFrame`/`returnToTop` get deleted with a ledger entry, and
never rebuild the cursor with a flag or `childFrames()`). New rules take the next
free number rather than renumbering: `assemble.py`, the tests and several
published reports cite rules by number.

**New tooling:** `eval_prompt_ab.py` + `scripts/compare_prompt_ab.py`, the mirror
image of the 6.3 reflection A/B. Every configuration key must match except the
file hashes, and among those only `docs/playbook.md` may have moved — so working
agreement rule 6 ("no prompt change without a green eval run") is checkable
rather than aspirational. Nine tests exercise the refusals.

```bash
uv run python scripts/run_eval_experiment.py --benchmark hard --model openai:gpt-5.4 --run
uv run python scripts/compare_prompt_ab.py <arm-a-dir> <arm-c-dir> --reserved 10 --reserved 11
```

**Two things left open on purpose:**

1. **Hard case 10 (the BasePage) failed all four runs** at the full three
   attempts. The model will not delete the wait helpers, and every TODO it wrote
   says why: *"call sites were not provided"*. Deleting `waitAndClick` is only
   correct if you can also fix its callers, which a single-file conversion
   cannot. That is a limit of the task shape, not a missing rule — forcing it
   with a rule would be fitting the prompt to a fixture. It belongs to suite
   mode (Phase 9).
2. **Hard cases 10 and 11 were reserved** from the tuning loop and must stay
   reserved. 10 did not move; 11 improved and is reported as probable variance.

**For the next session:** re-run on Opus when account access returns, and
re-measure rules 26/27 on a fresh set — with eleven rows, overfitting is a live
risk that only new fixtures can settle.

### 11.1a is done: the twelve hard cases are a second benchmark

Read [hard-cases.md](hard-cases.md) first. In one paragraph: the Phase 6.1 set
measures ordinary page objects and tests and the converter passes it, which is
not the same as being good at this job. `plan-review.md` listed twelve patterns
where a mechanical translation compiles, passes lint, passes residue, and
silently tests something else. Those are now fixtures.

```
samples/selenium-hard-suite/       11 Selenium files (the inputs)
samples/playwright-hard-golden/    11 Playwright goldens (the answers)
cd samples && npm run test:hard && npm run test:hard-golden
uv run python scripts/measure_hard_fixtures.py     # re-measure; rewrites the evidence file
uv run python scripts/upload_hard_dataset.py       # local preview, no network
```

**Uploaded and verified: `selenium2playwright-hard-v1-b233d4c101fd`, 11
examples** (receipt: `docs/phase-11.1-receipt.json`). Measured 2026-09-08:
Selenium 7/7 in headless Chrome (21.1s), Playwright goldens 7/7, 0 flaky
(14.7s), all four gates green over the whole golden tree.

Three things a future session must not undo:

1. **Hard cases 2 (dialogs) and 5 (windows) are covered by the Phase 6.1 set**
   and cross-referenced in `COVERED_BY_BASE_DATASET`, not duplicated.
   `check_manifest` fails the build if a case is claimed by both benchmarks or
   by neither, so "twelve" is enforced. Do not "fix" the coverage gap by
   writing a second alerts page.
2. **`tests/shared-session.spec.ts`'s golden carries a `TODO(review)` on
   purpose** (`storageState` is a suite-level fix a single-file conversion
   cannot make). `plan-review.md` finding 6: a dataset whose references never
   admit a limitation teaches the agent to guess confidently. That TODO is the
   feature.
3. **`snapshot_example` gained `source_dir`/`golden_dir` and
   `upload_collection` gained `description`** — both additive, both defaulting
   to the Phase 6.1 values. `BaseDatasetUnchangedTests` asserts the published
   set still fingerprints to `selenium2playwright-v1-4920b5f319d8`. If that
   test ever fails, the 6.1 dataset has silently moved.

**Honest gap, written down so nobody claims otherwise:** real Selenium v3
promise-manager code cannot be browser-verified under selenium-webdriver v4, so
hard case 12 uses returned promise chains (no `await` keyword in the file, same
conversion task). Implicit v3 ordering is not covered. Under hard case 8 only
hover is exercised — drag-and-drop and modifier clicks are still open.

### ⛔ 11.1b is blocked: the Anthropic account has hit its usage limit

Varun chose **Opus for both** the iteration and the published number on
2026-09-08. The plumbing shipped (`53ec192`) and the baseline was launched.
Every one of the 11 rows failed in under a second:

```
400 invalid_request_error: You have reached your specified API usage limits.
You will regain access on 2026-10-01 at 00:00 UTC.        req_011Ceq2Ec5hDUtBYe5H3mXJy
```

**It is account-wide, not Opus-specific.** A one-token probe on
`claude-sonnet-5` returns the identical error. **The live deployment is down
too** — `uv run python scripts/call_deployment.py samples/selenium-hard-suite/pages/HoversPage.ts
--url https://s2p.fly.dev` came back `needs-review after 1 attempt, gates (none
ran)`. So <https://s2p.fly.dev> and the playground currently convert nothing for
anybody. Fly is still billing for the machines.

**Zero tokens were spent** on the failed run. Its LangSmith experiment,
`s2p-11.1b-claude-opus-5-attempts3-f6b74c60`, is an **outage record, not a
converter score** — never compare a later run against it. Artifacts:
`out/11.1b/baseline-opus/`.

**Three ways out, all Varun's call:** raise or remove the limit at Console →
Settings (this is very likely the spend cap he was advised to set after 10.4);
wait for 2026-10-01; or run on OpenAI with `--model openai:...`, which he
explicitly did *not* choose — a cross-provider number is not comparable to the
6.2–6.5 baselines.

**What already works, ready to run the moment access returns:**

```bash
uv run python scripts/run_eval_experiment.py --benchmark hard            # preview, no network
uv run python scripts/run_eval_experiment.py --benchmark hard --run      # the baseline
```

`BENCHMARKS` in `eval_plan.py` binds each collection builder to its upload
receipt, so an edited fixture stops the run rather than scoring the converter
against rows that are no longer in the cloud. Reports now carry a
`by_hard_case` dimension and a **Per hard case** table. Those groups **overlap
on purpose** — a row exercises several patterns, so the group counts do not sum
to the experiment total; the question is "is pattern 7 handled?", not "did file
X pass?".

**Then, in order:** run the baseline, publish the per-hard-case scorecard
*including failures*, then iterate the playbook — every change gated by a green
eval run (working-agreement rule 6). Reserve at least two hard cases from the
tuning loop so the benchmark does not become the thing the prompt is fitted to.

The managed platform is the part that failed, and it is history now: 10.1 put
the graphs behind `langgraph dev`; 10.2 made them *deployable*: the file travels
as text (`source_text`), and the image carries the pinned Node toolchain the four
gates shell out to. Verified against the REAL deployment image on the REAL stack
(`langgraph build` + `langgraph up --image` + postgres + redis) — a file sent as
text came back 4/4 gates + critic pass with every gate running `tsc` inside the
container.

### ⛔ DO NOT RETRY `langgraph deploy`. Read [deploy.md](deploy.md) §9 first.

LangSmith Deployment **was** enabled, and we deployed for real on 2026-09-07.
**Six revisions across two deployments, ~2h35m, zero URLs, zero errors from our
code.** The serverless tier sets `CORE_API_GRPC_SIDECAR=1` and never starts the
sidecar, so the server dies waiting on `127.0.0.1:50051`. We fixed that (a `RUN
sed -i` unset in `dockerfile_lines`), hit a second wall (`CREATE EXTENSION
vector` needs superuser, which their Postgres will not grant), dropped the
`store` block, got the container **running healthily for 19 minutes** — and the
control plane *still* refused to assign a hostname. Their readiness gate wants
the same missing sidecar. Both deployments were deleted so nothing bills.

§9 of deploy.md has the full anatomy, the local one-command reproduction, and a
list of the things that *look* like fixes and are not (`api_version` pinning is
ignored on the remote build path; retrying; the web UI; `--engine-runtime-mode`).
**If a future session is tempted to "just try deploying again", that section is
the answer.** Only revisit the managed platform if LangChain announces a fix.

**The path forward is self-hosting, and it is strictly better here:** the bug
does not exist off that platform (the entrypoint runs the Go core in-process
whenever the variable is unset, which is everywhere else), and a self-hosted
Postgres is ours, so `CREATE EXTENSION vector` succeeds and the `store.index`
block comes back. Varun asked for Render vs Fly.io pricing — see
[deploy.md](deploy.md) §9 for the stack shape (this image +
`pgvector/pgvector:pg16` + `redis:6`, `linux/amd64`).

The playground never needed any of this settled: it points at whatever
`LANGGRAPH_DEPLOYMENT_URL` says, a local `langgraph up` or the Fly deployment,
and cannot tell the difference.

Read [deploy.md](deploy.md) and [local-platform.md](local-platform.md) first —
that is all of Phase 10 so far — then [suite-report.md](suite-report.md),
[suite-fanout.md](suite-fanout.md) and [suite-scan.md](suite-scan.md) for Phase
9, then [cli.md](cli.md) and [config.md](config.md), then
[long-term-memory.md](long-term-memory.md),
[human-in-the-loop.md](human-in-the-loop.md) and
[short-term-memory.md](short-term-memory.md).
[phase-9.3-report.md](phase-9.3-report.md) is the live artifact the whole
milestone builds toward — read it to see what the tool actually hands a person.

Commit/push authorization persists. The 150-line / one-file-at-a-time rule was
removed by Varun on 2026-09-06: complete the whole step when asked, then one
plain-English walkthrough with check-yourself questions, then wait for his
review. **Always end a turn that hands control back with an explicit "waiting on
you" line** (asked 2026-09-06 — a pause must never be implied).

**448 offline tests pass** (`uv run python -m unittest discover -s tests`, ~163 s
— not `-t .`, and pytest is not installed). The suite is terminal-width
independent from 40 to 200 columns as of 9.3.

## What 10.2 built (deployability)

- **`source_text` + `context_text`** on `ConversionState`, read by
  `graph.read_inputs`. When text is sent, `source_path` is only a **name**;
  everything downstream (classify, `recall_query`, scorecard, report) only ever
  wanted the name, so nothing else in the graph changed. **Neither input is
  consumed** — unlike `refinement`/`remember`, they are what the conversation is
  *about*, so a refine turn still needs only a sentence. `PASTED_NAME` =
  `pasted.ts` when no name is sent.
- **`graph.oversized()` + `MAX_SOURCE_BYTES` (256 KB)** — returns a
  `Classification(supported=False)` rather than raising, so an over-long paste
  leaves through the `refuse` node. Counts **bytes**, not characters.
- **`env.REPO_ROOT` + `env.SANDBOX`** (honours `S2P_SANDBOX`). `compile.py`,
  `lint.py`, `parity.py` and `assemble.py` each used to compute
  `REPO_ROOT / "sandbox"`; all four import the one object now, and `prompts.py`
  imports `REPO_ROOT` from `env` instead of computing its own.
- **`langgraph.json` `dockerfile_lines`** — Node 22 copied from
  `node:22-bookworm-slim`, `npm ci` from the lockfile into `/opt/s2p-sandbox`,
  `ENV S2P_SANDBOX` pointing at it. **`.dockerignore`** excluding `.env` and
  `node_modules/`.
- **`scripts/call_deployment.py`** — `langgraph-sdk`, reads the file locally,
  sends the text, streams node names, prints the scorecard. `--url` is the only
  thing that differs between a local container and a cloud deployment.
  Deliberately a script, not an `s2p` subcommand: 10.3's playground is the real
  remote front end.
- `tests/test_platform.py` grew to **30** (deploy-config guards, sandbox
  override, inline inputs, the size cap). **331 total.**
- **Live:** image 1.6 GB, Node v22.23.2, tsc 5.9.3, ESLint v10.10.0 at
  `/opt/s2p-sandbox`; all four gates ran in the container (a broken file failed
  compile with a real tsc message); a full conversion over the SDK against
  `s2p:10.2` + pgvector/pg16 + redis:6 → 3 attempts, 4/4 gates + critic pass,
  1 honest locator TODO, `needs-review`, exit 1.

## 10.2 sharp edges

- **An apostrophe in a `langgraph.json` graph description breaks the build.**
  The CLI writes `ENV LANGSERVE_GRAPHS='{...}'` single-quoted and unescaped;
  "the SERVER's filesystem" closed the string and Docker said
  `Syntax error - can't find = in "filesystem"`, pointing at nothing
  recognisable. A test now forbids `'` in descriptions.
- **`dockerfile_lines` are inserted after `FROM` and BEFORE
  `ADD . /deps/<project>`.** They can install, but cannot run anything against
  the repository. `COPY` still reaches the build context, which is what makes
  the lockfile-first (cacheable) `npm ci` layer possible.
- **`/deps/<project>` is named after the directory the build ran in** —
  `Selenium2Playwright` here, `selenium2playwright` from a fresh clone. Hard-coding
  either ships a build broken for everyone else; hence the fixed
  `/opt/s2p-sandbox` plus `S2P_SANDBOX`.
- **Never let `node_modules` into the build context.** It is installed inside
  the image on Linux; a macOS tree copied in would shadow it with the wrong
  platform's binaries.
- **`.env` must be dockerignored.** A key baked into a layer cannot be rotated
  out of the layers that already exist.
- `langgraph up --image <tag>` runs an image you already built (with postgres +
  redis) — that is the dress rehearsal, and it is where every problem above was
  found. This laptop is **arm64**; `langgraph deploy` builds remotely for
  linux/amd64 when it has to.
- Docker Desktop must be running (`open -a Docker`); the daemon is not up by
  default on this machine.

## What 10.1 built (`server.py`, `langgraph.json`)

- **`langgraph.json`**: `dependencies: ["."]`, two graphs (`convert`, `suite`)
  with human-facing `description`s, `env: ".env"`, and a `store.index` block
  (`embed` -> our own function, `dims` 1536, `fields: ["text"]`) that is what
  makes 7.3's semantic recall work over HTTP.
- **`server.py`**: two **zero-argument** factories plus `embed_memories`.
  Nothing else; the graphs are unchanged.
- **`store.indexed()` unwraps one layer** (`_store`) — the real bug this step
  found, see the sharp edges below.
- `tests/test_platform.py` (13). `.langgraph_api/` gitignored.
  `langgraph-cli[inmem]` added as the first `dev` dependency group.
- **Live over HTTP on `langgraph dev`:** `POST /runs/wait` on `convert` —
  3 attempts, 4/4 gates + critic pass, 6 honest locator TODOs, 16,649 tokens
  with 5,380 cache-read. `suite` with `only: ["LoginPage.ts", "login.spec.ts"]`
  — 2 waves in dependency order, both passed in 1 attempt, whole tree compiled,
  `conversion-report.md` written. `ask_risks: true` on a thread returned the
  dialogs `__interrupt__` with its three options. A memory written via
  `PUT /store/items` searched back at 0.5224 and reached the `recall` node at
  0.4986.

## 10.1 sharp edges

- **A graph factory's signature is its API, and it is read by annotation.**
  `_classify_factory` allows 0-2 params, a `RunnableConfig` and a
  `ServerRuntime`, identified by type hints. `build_graph(checkpointer, store)`
  — two unannotated — raises at server start. `build_suite_graph(store)` — one
  unannotated — **silently passes a RunnableConfig as the store**. Zero-argument
  wrappers are the only unambiguous shape.
- **The platform owns persistence.** Compile with no checkpointer and no store;
  it injects both at run time. The local dev server *refuses* a graph that
  brought its own. 7.1/7.3's optional `= None` parameters are what made this a
  no-change deploy.
- **`store.index.embed` wants a `texts -> vectors` function, not a factory.**
  `ensure_embeddings` wraps any plain callable as the former. Pointing it at
  `llm.make_embeddings` would be wrong in both directions.
- **`dims` cannot be computed in JSON.** A test pins it to
  `llm.EMBEDDING_DIMS[env.DEFAULT_EMBEDDINGS]` so a changed default fails in the
  suite, not at run time. `S2P_EMBEDDINGS=off` conflicts with an index and now
  raises naming both settings.
- **The platform's store is a `BatchedStore` with no `index_config` attribute at
  all**, so `indexed()` answered False and every platform recall would have
  degraded to recency — no error, no failing test, a demo that looks fine. Only
  running the graph somewhere else could surface it.
- **Not fixed here, and 10.2's first job:** `source_path` is read on the
  *server's* filesystem. Fine locally, meaningless in the cloud.
- Studio's input form offers all 30 `ConversionState` keys (`total=False`); an
  explicit `input_schema` would fix it but filters incoming keys, so it needs
  the eval runner and 314 tests in view. Suite runs over HTTP get the platform's
  default recursion limit of 25, not `cli.py`'s `2 * waves + 6`.

## What 9.3 built (`assemble.py`, the last step of M4)

- **The argument.** Every per-file gate verdict is a *local* claim: this file
  compiled on its own, against the companions it happened to import. Two files
  that each compile perfectly can refuse to compile together, and nothing in the
  project could see it. So the suite graph's `finish` node now compiles the
  **delivered tree as one project** (`compile_tree` → the pinned
  `tsc --noEmit` over all of `out_root`), and **exit 0 requires both**:
  `not failed and built.compiles`. A compile that could not run is
  `compiles=False` — unknown is never green.
- **Parity ledger.** `sandbox/members.cjs` is a second parse-only sandbox script
  (never imports or runs submitted code, same rule as `parity.cjs`) reporting
  each file's public surface: non-private class members, `public` constructor
  parameter properties, top-level exported functions and constants. Source names
  are matched to converted ones as kept / renamed / removed; a removal's reason
  is **quoted verbatim** from the model's own `notes`/`todos` when one of them
  names the member, else the report says **no reason given**. `FileOutcome`
  gained `notes` for exactly this. Test identities come free from a second call
  to the existing `parity.cjs`.
- **Rename detection is the one guess, and it is deliberately timid.** `stem()`
  strips accessor prefixes (get/set/is/waitFor/verify…) and type-ish suffixes
  (Text/Value/Element/Locator); containment of one stem in the other scores 1.0,
  otherwise `SequenceMatcher`, cutoff `RENAME_RATIO = 0.7`. So
  `getFlashText → flashMessage` is a rename and `open → goto` is a removal plus
  an addition. **A wrong rename hides a loss; a missed one is only noise.**
  Members pair only *inside the same class* (class renames are matched first, so
  `LoginPage → LoginPageObject` is not a bloodbath), and members and tests are
  matched in **separate pools** — a lost test can never be explained away as a
  renamed method.
- **Consolidated TODO(review) ledger** (playbook rule 25): read from *both* the
  comments in the written code (with line numbers, whole comment blocks) and the
  `todos` each conversion reported, then merged by **containment**, not equality.
- `s2p.suite-report/v1` is a strict **superset** of 9.2's `s2p.suite-run/v1` —
  same keys, new schema name, plus `tree` / `scorecard` / `parity` / `todos`.
  Markdown goes to `<out>/conversion-report.md`; `--report FILE` moves it.
- `tests/test_assemble.py` (26). The one to read first builds two files that
  each compile perfectly alone and cannot compile together; a CLI test proves
  that turns two green rows into exit 1.
- **Live 2026-09-07**, all 12 sample files, `--parallel 4`, Sonnet: 9 passed /
  3 needs-review, exit 1, **83.1 s**, the tree compiles, **20 public names kept,
  8 renamed, 0 removed** — all eight the same idiom shift
  (`getResultText → resultMessage`). Two distinct TODOs, one of them reported by
  *both* `tests/login.spec.ts` and `pages/LoginPage.ts` and collapsed to one
  line. Trace `01a07aa0-d597-75e1-bdfb-886593881b7e`: one trace, 557 runs, 32
  LLM spans; `--parallel 4` is visible in it (first four branches start within
  17 ms, the fifth 16 ms after the first finishes), wave 2 starts 28 ms after
  wave 1's slowest, and the whole `finish` assembly costs **0.63 s** of the 83.
  Report committed verbatim as `docs/phase-9.3-report.md`.

## 9.3 sharp edges

- **`members.cjs` uses `parity.cjs`'s stdin protocol**: a *list* of `{path:
  source}` maps in, the same list of inventories out, so one node process
  inventories both sides. Passing a single map made `ts.createSourceFile`
  receive an object and die inside the scanner.
- **A wrapped `// TODO(review):` comment only had its first line captured**, so
  it never matched the full sentence the model reported and one task showed up
  as two. Fixed with `todo_blocks()` (consume continuation comment lines) plus
  the containment merge. **No test caught this — only reading the live output
  did.** Read the real artifact before believing the test suite.
- **Three pre-existing width-dependent failures in `test_cli.py`** surfaced at
  COLUMNS=40/60: fixed with `test_store`'s `unwrapped()` helper and by pinning
  `cli.console.width = 100` in `CliHarness.setUp`.
- **Assembly is a graph node, not CLI code.** The graph produced the tree, so
  the graph says whether the tree holds together — and the whole-tree compile
  lands in the trace beside the conversions that made it necessary.

## What 9.2 built (`suite_graph.py` — Send, reducers, subgraphs)

- Shape: `START → plan → next_wave --dispatch--> [convert_file × N] → next_wave
  … → finish`. **`Send`**: a conditional edge may return a *list* of
  `Send(node, payload)` instead of a node name, so the fan-out width is data
  (the wave), not wiring; the payload **is** the branch's whole input state (it
  is not merged into the parent), hence
  `add_node("convert_file", convert_file, input_schema=FileJob)`. Sync mode runs
  the branches on a real `ThreadPoolExecutor`.
- **Reducer**: N branches writing one key is `InvalidUpdateError: Can receive
  only one value per step`; `outcomes: Annotated[list[FileOutcome],
  operator.add]` is the join. Two consequences: results arrive in **completion
  order** (`ordered()` re-sorts by `(wave, path)` at read time), and a reduced
  channel can only be appended to, never rewritten — so the sort can never live
  in a node.
- **Subgraph**: `convert_file` invokes `graph.build_graph()` with
  `run_name=f"convert:{path}"`, so LangSmith nests suite → file → attempt → LLM.
  `SuiteSettings` context (model / critic_model / max_attempts / user_id) is
  copied into each child's `RunSettings`.
- `plan` copies `copy`-action files into `out_root` **first** (they are context);
  `dispatch` builds each job's `context_paths` from `out_root` **at dispatch
  time**, which is how wave 2 sees the *converted* companion. A needs-review file
  is still written (the next wave imports it); a branch that raises becomes a
  `failed` row, never a dead suite; `ask_risks=False` always (twelve parallel
  branches have nobody to interrupt).
- `--only PATTERN` (fnmatch on the relative path or the bare name, repeatable),
  `--parallel N` → `config["max_concurrency"]`, `recursion_limit = 2*waves + 6`.
- **Gotcha:** `from __future__ import annotations` makes
  `SuiteState.__annotations__["outcomes"]` a *string*, so pinning the reducer in
  a test needs `get_type_hints(..., include_extras=True)`.

## What 9.1 built (`suite.py` — the scan, no LLM)

- `SuiteFile` / `Manifest`; `discover` (os.walk with `SKIP_DIRS` **pruned from
  the walk**, not filtered after); `import_specifiers` (one regex covering
  `from "x"`, `import "x"`, `export … from`, `require()`, dynamic `import()`);
  `resolve_import` (TypeScript's own order `.ts .tsx .js .mjs .cjs /index.ts
  /index.js`; a non-`.` specifier → `external_imports`; a relative path leaving
  the folder → `None`, a real limitation the report names).
- **The two readings of `classify()`**: a helper with no automation library gets
  `supported=False` from the classifier (right answer to "convert this file")
  but action **copy** from the scanner — "nothing to convert" in a folder is not
  a refusal. Four kinds → three actions; copied and skipped files are in no wave.
- **Waves are Kahn's algorithm layer by layer**, over convertible files only; an
  import cycle gets one final wave plus a note rather than a hang or a silent
  drop. `imports` does double duty: the wait-for edge *and* the file's
  `context_paths`. `imported_by` is for blast-radius reporting.
- `s2p scan <folder>`: table on stderr, `s2p.suite-manifest/v1` on stdout under
  `--json`. The toy 6-file mixed suite is built in a `TemporaryDirectory` inside
  `tests/test_suite.py`, deliberately **not** in `samples/` (whose
  `tsconfig.json` includes `**/*.ts`, so a broken fixture there would fail
  `tsc`). Note it is a **three-deep** chain (BasePage → LoginPage → spec) = 3
  waves.
- **Gotcha that cost two test failures: rich/Typer output is terminal-width
  dependent.** In a `Table` the cells *interleave* when folded, so no string
  trick can reassemble a path — pin the width instead
  (`cli.console.width = 100` in `setUp`, restore `cli.console._width` in
  cleanup; `patch.object(console, "width", …)` fails, the property has no
  deleter).

## What 8.2 built

- `graph.RunSettings` (frozen dataclass: `model`, `critic_model`,
  `max_attempts`) declared as `StateGraph(..., context_schema=RunSettings)`,
  passed per call as `compiled.invoke(inputs, config=…, context=run)`.
- `graph.settings(runtime)` — the required guard: LangGraph does **not** apply
  a context schema's defaults, so `runtime.context` is `None` on any invoke
  without `context=` (every earlier phase, the eval runner, most tests).
- `intake` is the only node that reads the context. It resolves both models and
  the cap once and records them in the state (`state["models"]`); `convert` and
  `critic` read that record via `graph.model_for`, so the label can never
  disagree with the call. A context cap beats one restored from a thread.
- `env.MODEL_ALIASES` (sonnet/opus/fable/haiku) + `env.resolve_model`,
  `env.resolve_roles` (flag > `.env` > default; `--model` moves the critic too
  unless a split was chosen deliberately) and `env.key_missing` (fail fast,
  masked, never prints a value).
- `llm.resolve_name` extracted so `make_model` and `prepare_messages` cannot
  disagree about which model a call is for.
- `cli.py`: `--model`, `--critic-model`, `--json`; `run_config()` puts the same
  choices on the trace as tags + metadata; `json_report()` + `emit()` (one
  stdout writer for every exit path, refusals included).
- `tests/test_config.py` (22, incl. cross-provider). **225 offline tests pass.**
- Live check: `--model haiku --critic-model opus` on `LoginPage.ts` — 2/3
  attempts, 4/4 gates + critic PASS, exit 1 on two `baseURL` TODOs; LangSmith
  showed two Haiku actor spans and two Opus critic spans with `.env` untouched.

## Cross-provider work (same day, after 8.2)

- The preflight runs on **every** convert, not only when `--model` is given: a
  machine whose only key is OpenAI now gets exit 2 and `cli.alternatives()`
  ("keys are set here for openai — run with --model openai:<model>") instead of
  exit 1 after a failed authentication. Every test harness now sets a fake
  `ANTHROPIC_API_KEY`, so the offline suite no longer depends on a real `.env`.
- `llm.check_model(name)` — the preflight: `env.key_missing` first (friendly,
  no imports), then **build the client** (local, no network) so the provider
  itself reports a missing integration package (message rewritten to
  `uv add langchain-x`) or an unsupported provider (LangChain's 28-name list is
  replaced with the shape of a model string).
- `env.PROVIDER_KEYS` now covers 14 providers, `KEYLESS_PROVIDERS` covers local
  and credential-chain ones, and an unlisted provider is **unverifiable, not
  invalid**: `required()` no longer raises, `unverifiable()` reports it, and
  `env.check()` prints it as a `•` line.
- `llm.structured_kwargs(name, for_critic)` — `method="json_schema"` only for
  providers where it is known to work (anthropic, openai); everywhere else the
  default tool-calling path. It used to be hard-coded in the critic node.
- Vendor-specific behaviour is now exactly three things, all in `llm.py`: the
  Anthropic cache marker, the critic `effort` knob, and that JSON-schema list.
- Live: `openai:gpt-5.4` alone on `LoginPage.ts` (4/4 + critic PASS, exit 0) and
  `openai:gpt-5.4` actor + `anthropic:claude-sonnet-5` critic on
  `login.spec.ts` (4/4 + critic PASS, exit 0) — two vendors in one graph, the
  Anthropic critic still getting its 3,301-token cache read. Third run with no
  Anthropic key in the process at all (`S2P_MODEL=openai:gpt-5.4`): 4/4 + critic
  PASS, exit 1 on four honest locator TODOs.
- Not checked before a run: the model *name* (needs a network call). Optional
  providers stay out of `pyproject.toml`; the error names the `uv add`.

## 8.2 sharp edges

- **A context schema's defaults are not applied.** `runtime.context is None`
  without `context=`; always go through `graph.settings()`.
- **`runtime` has a `= None` default** so nodes remain directly callable in
  tests; LangGraph still injects by parameter name.
- **The lap budget has two channels**: context (CLI) and the `max_attempts`
  state input (eval harness, since 6.3). Context wins when set.
- **`--json` must own stdout alone** — never the document *and* the code.
- **Provider keys are only checked when a model is named on the command line**;
  probing on every run would break every offline test.

## What 8.1 built

- `cli.py` (483 lines) — the only front end. Five commands: `convert`,
  `remember`, `memories`, `forget`, `threads`. `[project.scripts]` now installs
  `s2p`; `python -m selenium2playwright.graph` is gone.
- `graph.py` 758 → 448 lines: no `main()`, no argparse, no `print()`. Its
  reporting helpers moved to `cli.py` and became rich renderings.
- Scorecard `Table` (compile/residue/lint/parity + critic + open-TODO count),
  verdict and reason on a plain soft-wrapped line above it, findings and fixes
  printed in full underneath, then a `Panel(Syntax(..., "diff"))` before/after —
  baselined on the *previous turn* when there is one, else the source.
- `one_shot.format_usage()` split out of `report_usage()` so both surfaces
  share the token line.
- `tests/test_cli.py` (12); 7 existing test files repointed from `graph.main`
  to `cli.run`, and `graph.make_embeddings` patches became `cli.make_embeddings`.
  **203 offline tests pass.**
- Live check: `uv run s2p convert samples/selenium-suite/pages/LoginPage.ts
  --out out/8.1/pages/LoginPage.ts` — Sonnet actor + critic, 2/3 attempts,
  4/4 gates + critic PASS, exit 1 on three honest locator TODOs.

## 8.1 sharp edges

- **rich parses `[...]` as markup.** Anything dynamic goes through `say()`,
  which builds a `rich.text.Text`; a test pins `[data-testid]` surviving.
- **Click always exits by raising `SystemExit`, even on success.** `cli.run()`
  catches it and returns the code — that is the seam every test uses, and it is
  why usage-error tests assert `code == 2` instead of `assertRaises`.
- **A passing gate can still carry findings** (lint warnings): the detail cell
  says `1 finding(s)` next to PASS, never "clean".
- **Table rows are not sentences.** Assertions match the row line
  (`compile[^\n]*PASS`), and the row helper filters on the box character so the
  prose verdict line mentioning "critic" is not mistaken for the critic row.

## What 7.3 built

- `store.py`: `Memory(key, text, score, created)`, `open_store` (SqliteStore +
  vector index; records which embeddings model built the file and refuses to
  open it with another), `remember`/`forget`/`memories`/`recall`, and
  `recall_query` — a distilled **profile** of the file (kind, name, class, test
  titles, methods, locator strategies, element ids, camelCase split into words),
  not the file itself.
- `graph.recall` between `intake` and `risk_review`, given the store by
  `compile(store=…)`. New state: `user_id`, `remember`, `recalled`,
  `memory_count`. A preference given this run is always sent; older ones must
  clear `MIN_SCORE`; anything already standing on the thread is excluded.
- `prompts.format_remembered` + a REMEMBERED PREFERENCES block ahead of
  conventions and decisions, to actor *and* critic, plus a rubric line saying an
  ignored preference is not a defect.
- Embeddings: `env.embeddings_name()` / `llm.make_embeddings()` /
  `llm.embedding_dims()`, default **`openai:text-embedding-3-small`**,
  `S2P_EMBEDDINGS=off` supported, Voyage a one-line swap. `langchain-openai` is
  now a direct dependency. `Memory` added to `memory.CHECKPOINT_TYPES`.
- CLI: `--remember`, `--memories`, `--forget`, `--user`, `--no-recall`,
  `--memory-db` (separate file from `--db`; the store outlives threads).
  *(8.1 turned the first three into `s2p remember` / `memories` / `forget`;
  `--remember`, `--user`, `--no-recall` and `--memory-db` stayed on `convert`.)*
- `scripts/calibrate_recall.py`, `scripts/demo_store.py`, artifacts in `out/7.3/`.
- Tests: `tests/test_store.py` (30); 191 total.

## 7.3 sharp edges (LangGraph/library behaviour, not our bugs)

- **`SqliteStore` reads `text_fields`, not the documented `fields`.** Give it
  only `fields` and it silently embeds `"$"` — the whole JSON document — so each
  memory's `source` path lands in its own vector and the same sentence scores
  differently depending on where it was written (0.3238 vs 0.3954, observed).
  `open_store` passes both keys.
- **`store: BaseStore | None` silently disables injection.** LangGraph matches
  the parameter by name *and by the literal text of the annotation*, against a
  list holding `"BaseStore"` / `"Optional[BaseStore]"` only. With
  `from __future__ import annotations` the modern union spelling matches nothing:
  the node runs, `store` is always None, nothing is ever recalled, no error.
- **A memory written to an unindexed store can never be found again** (vectors
  are computed on write). `--remember` therefore hard-fails when embeddings
  cannot be loaded; reading degrades to recency and says so.
- **`SqliteStore.setup()` needs `isolation_level=None`** or its migrations raise
  "cannot start a transaction within a transaction".

## Recall calibration (measured, `out/7.3/recall-calibration.json`)

The naive "paste the head of the file" query separates nothing: lowest genuinely
applicable 0.2962 sits *below* the loudest unrelated 0.3044. The profile query
fixes the ranking (naming wins on page objects, fixtures on specs, noise at the
bottom). The bands still overlap, so the verdict is a **shortlist** check, not a
threshold hunt: at `MIN_SCORE = 0.29`, cap 3, the six sample files recall
**11 of 15 applicable and 0 of 18 unrelated**. Every miss is one vaguely worded
memory: prefixing the same rule with "In test specs," moved it 0.281 → 0.320.
Re-run the script after changing the embeddings model or the query.

## Live 7.3 demo (Sonnet actor + critic, `out/7.3/`)

Taught while converting `pages/LoginPage.ts` on thread `monday`, applied by
itself on `tests/upload.spec.ts` in fresh thread `wednesday` with nothing typed:
`test.step` blocks, **25 lines** different from the storeless control, all three
arms **4/4 gates + critic pass in 1 attempt**, +394 actor tokens for the recall.
Three of four memories correctly stayed behind (the vague twin, a page object
rule, and the CI/S3 noise). Receipt `out/7.3/demo-receipt.json`.

## What 7.2 built

- `risk.py`: `Risk(kind, line, snippet, count)`, the `RISKS` catalogue (three
  kinds — `dialogs`, `javascript-execution`, `shared-session` — each with a
  question and three answer options written as guidance sentences for the
  model), `detect_risks` (regex, deterministic, one question per kind),
  `question()` (interrupt payload), `resolve()`, `decision_lines()`.
- `graph.risk_review` between `intake` and `convert`: one `interrupt()` per
  unanswered risk. New state keys `ask_risks` (off by default), `risks`,
  `decisions`. `decisions` persists on the thread like `conventions` and is
  rendered into the actor *and* critic prompts by `prompts.format_decisions`.
- CLI: pauses only on a `--thread` run; `--answer KIND=ANSWER`, `--no-ask`; a
  run with no terminal prints that it used the default instead of guessing
  silently. `Risk` added to `memory.CHECKPOINT_TYPES`.
- `samples/risky/secure-area.spec.ts` — non-eval fixture for the two kinds the
  pinned suite does not contain. Of the 12 eval files only AlertsPage.ts flags.
- `scripts/demo_hitl.py`, artifacts + receipt in `out/7.2/`.
- Tests: `tests/test_risk.py` (25); 161 total.

## 7.2 sharp edges (not in gap-log; LangGraph behaviour, not our bugs)

- A **paused `invoke()` returns what that invocation wrote plus
  `__interrupt__`, and no report** — not the finished state. Loop on
  `"__interrupt__" in result`.
- **Resume values are matched to `interrupt()` calls by position.** Two
  questions in one node only stay correct because `detect_risks` is pure and
  ordered. Never make the question list depend on anything that can change
  between a pause and a resume.
- `state.next` returned `()` after a *second* interrupt while the task was
  still pending with interrupts on it. Use the reply's `__interrupt__` or
  `state.tasks[…].interrupts`.
- `interrupt()` requires a checkpointer; there is nowhere to suspend to
  without one.

## Live 7.2 demo (Sonnet actor + critic, `out/7.2/`)

Same file (`AlertsPage.ts`), two answers, one question each, both arms
**4/4 gates + critic pass, no TODOs, 30 lines different**. `handler-first`:
`page.once("dialog", …)` before the click, 2 attempts — attempt 1 failed the
lint gate (`no-floating-promises`: a synchronous handler cannot await),
attempt 2 added `void` plus a `lastDialogMessage` field the critic asked for;
11,523 actor tokens. `expect-event`:
`Promise.all([waitForEvent("dialog"), click()])`, 1 attempt, 4,702 tokens. The
model's own notes cite "Human decision 1". Four traces (two per arm — a resumed
run is a second trace) in `out/7.2/demo-receipt.json`.

## What 7.1 built

- `memory.py`: `open_checkpointer` (context manager, makes the dir, strict serde
  + `CHECKPOINT_TYPES` allowlist), `thread_config`, `thread_state`,
  `list_threads`, `strict_serializer`.
- `graph.build_graph(checkpointer=None)` — the default is the old stateless
  graph, so evals and every earlier phase are untouched.
- New `ConversionState` keys: `refinement` (input), `turn`, `conventions`,
  `baseline`. `intake` is the turn boundary; `convert` gained a third entry
  (`reflection.refinement_feedback`); `critic` sees the conventions too.
- `prompts.format_conventions` + `build_prompt(conventions=)` +
  `build_critic_prompt(conventions=)`; new critic rubric line about standing
  instructions. Prompts are byte-identical to Phase 6 when there are none.
- CLI: optional `source`, `--thread`, `--refine`, `--db`, `--list-threads`
  *(8.1: now `s2p threads`)*, remembered `--out`. `.s2p/` gitignored; dependency `langgraph-checkpoint-sqlite`.
- `scripts/demo_memory.py` — real two-turn run, artifacts + receipt in `out/7.1/`.
- Tests: `tests/test_memory.py` (16); 136 total.

## Gotcha worth remembering (not yet in gap-log)

`BaseCheckpointSaver.with_allowlist()` does nothing on its own: the default
serializer allows every type (with a deprecation warning), and an allowlist on
top of "everything" is still everything. Strict mode is normally reached via
`LANGGRAPH_STRICT_MSGPACK`, read **once at import**, so patching it in a test is
too late. `memory.strict_serializer()` asks for strict directly. A type missing
from `CHECKPOINT_TYPES` fails *quietly*: it comes back as a plain dict and the
`AttributeError` surfaces much later.

## Live 7.1 demo (thread `demo-login`, Sonnet actor + critic)

Turn 1: source path only → 3 attempts, 4/4 gates, critic revise ×3,
needs-review (attempt cap). Turn 2: `{"refinement": "Use getByTestId() …"}` and
nothing else → 2 attempts, 4/4 gates, critic **pass**, needs-review (open
TODOs). `getByTestId` applied to username/password/flash; the submit button had
no id, so the agent kept `locator("button[type='submit']")` with a
`TODO(review)` instead of inventing a test id.

## Earlier state (6.4 and 6.5), kept for reference

## What the Haiku step built (commit `1c8edad`)

- `env.critic_model_name()` / `env.model_names()`: `S2P_CRITIC_MODEL`, empty
  means "same as `S2P_MODEL`"; `env.required()` covers every provider in use.
- `llm.make_model(for_critic=True)` and `llm.prepare_messages(for_critic=True)`
  resolve the critic's model; `graph.critic` uses the latter.
- `eval_plan.configuration(..., critic_model)` hashes `critic_model`;
  `build_plan(..., critic_model=)`; metadata `models` lists both.
- `run_experiment` refuses a `S2P_CRITIC_MODEL` that disagrees with the plan
  and adds `-critic-<model>` to the prefix only when it differs.
- `eval_compare` carries `critic_model` and `phase`; `run_reflection_ab.py`
  gains `--critic-model` and `--phase` (output under `out/<phase>/`).
- Tests: `tests/test_model_split.py` (7); 106 total.

## Added after the Sonnet run (same day)

- `eval_compare`: any row whose error starts with `Error code: ` (provider
  HTTP error) marks the arm non-comparable ("rerun that arm").
- `eval_shootout.py` + `scripts/render_actor_shootout.py`: SVG + markdown
  table across actors from comparison receipts; refuses non-comparable
  receipts and mixed critics. Output `docs/reflection-shootout.svg`,
  `docs/reflection-shootout-table.md`, embedded in README.
- Tests: `tests/test_eval_shootout.py` (2), provider-error test in
  `test_reflection_ab.py`; 109 total.
- Sonnet, valid run at `52256a9` (third attempt; first hit a credit failure,
  T10; second hung after the Mac slept, killed; run live experiments under
  `caffeinate -i -s`): A `5773296b-6650-4850-a5a3-8d716adc5e1e`, B
  `fedf9a3c-ac1a-4316-a8df-11fd6c94563a`; static 12/12 both; graph 11/12 →
  10/12 (login-page: critic variance then TODOs); 3 repairs; cost $0.289 →
  $0.404. Receipt `docs/phase-6.5-sonnet-comparison.json`; report
  `docs/phase-6.5-sonnet-report.md`; artifacts
  `out/6.5/ab-20260906T231129Z-98298800/`.
- `docs/reflection-shootout.md` is the one-page reading of all three actors.

## Live evidence (revision `1c8edad`, clean)

A `s2p-6.5-claude-haiku-4-5-20251001-critic-claude-opus-5-attempts1-cd1eedf0`
/ `5cbc2e15-0208-494f-9fe8-9827faa72319` / config `d84e5fae…`; B
`…-attempts3-6b251c97` / `332774ac-fe8f-4358-baa2-ae8aee0a74aa` / config
`61ea0ba1…`. All-static 9/12 → 12/12 (compile 9 → 12, lint 11 → 12); graph
passed 2/12 → 9/12; repairs used on 8 rows (5 two attempts, 3 three);
7 improved with repair, 2 variance, 3 same, 0 regressed. Actor calls 12 → 23,
actor tokens 42,510 → 95,328, critic tokens 62,374 → 119,489, wall-clock
199 → 391 s, cost $0.357 → $0.689 (complete both arms). Three-way against
6.3 run 2: Haiku+reflection (9/12) beats Opus alone (6/12), trails Opus +
reflection (11/12), costs more. Receipt `docs/phase-6.5-haiku-comparison.json`;
screenshot `docs/phase-6.5-haiku-delta.jpg`; artifacts
`out/6.5/ab-20260906T085549Z-5764b5b4/` (ignored).

Discarded: `out/6.5/discarded-dirty-worktree-ab-20260906T085156Z-1b8daf8f/`
(orphan LangSmith experiment `e931ca7e-…`), rejected by the runner because a
doc file was created in the worktree mid-run. **Do not touch the repo while
a live experiment is running.**

## Open observations for 6.4

- Critic verdict on POMs is unstable between runs (4 of 6 in 6.3, 2 of 12
  here). Calibrate the rubric judge against the goldens before trusting it.
- The critic dominates cost when the actor is cheap; a Haiku-critic arm was
  deliberately not run (two variables at once).
- LangSmith UI averages can drop rows; quote the receipts.

## Working agreement and environment

Teach theory before code in **plain, simple English** — assume no LangChain
knowledge and explain every construct as it appears. The 150-line /
one-file-at-a-time rule was removed on 2026-09-06: complete the whole step when
asked, then give one walkthrough with check-yourself questions, and let Varun
review before the next step. Review questions target **agentic workflows /
LangChain / LangGraph / LangSmith** — never SDET or TypeScript fundamentals,
which are his home turf. **End every turn that hands control back with an
explicit "waiting on you" line**; he has twice had to ask "stuck?" after a
finished step. No agents unless asked. Frequent progress updates.

Existing commit/push authorization persists; check `gh auth status` is on
`varunbhatt2193` before pushing (the active account flips between his projects
on this Mac). Repo `/Users/varunbhatt/Downloads/Selenium2Playwright`, main,
remote `https://github.com/varunbhatt2193/selenium2playwright.git`. `.env`,
`out/`, `roadmap.md`, `plan-review.md` stay ignored; never read `.env` or print
a key value; never re-add `roadmap.md` / `plan-review.md` to the repo (Varun's
call — not recruiter-friendly; the public face is README.md + plan.md).

**Read `git diff --cached` before every commit.** Varun stages work in progress
while Claude works, and a bare `git add -A && git commit` once swept his staged
deletions into a commit and broke main. `src/selenium2playwright/bbb.py` — the
Streamlit scratch file this warning used to name — no longer exists, but the
habit stands: check the index first, then commit with an explicit pathspec.
`S2P_MODEL` = actor, `S2P_CRITIC_MODEL` = critic (optional),
`S2P_EMBEDDINGS` = recall (default `openai:text-embedding-3-small`, `off`
supported); eval CLIs default to Opus. Use the existing `.venv` and Node toolchains. Chrome
computer-use works for LangSmith screenshots (crop the sidebar with `sips`,
offset 208 px); close tabs when done.
