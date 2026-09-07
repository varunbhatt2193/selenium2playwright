# 🚧 Selenium2Playwright — work in progress

**An AI agent that converts TypeScript Selenium test suites to Playwright** — a single test, a page object, or the whole suite — built on LangGraph + Claude, with a self-correcting loop that validates its own output before you ever see it.

> **Status: building in public.** Phases 0-8 of 12 complete — **bounded reflection is working**. The graph converts, validates, and reviews code, then repairs it using the actual findings, for at most three conversion attempts (configurable down to one for evaluation). Assembly always reports the outcome and retains the latest available draft. A live seeded demo repaired a missing `await` on attempt 2: all four gates and the critic passed, while two locator TODOs correctly kept the final status at `needs-review`. See the [reflection walkthrough and demo](docs/reflection-loop.md).
> Architecture & decisions: [plan.md](plan.md)
> **In one line:** the agent checks and fixes its own work, and I measured that this self-correction lifts a cheap model from 2 of 12 correct conversions to 9 of 12, while a strong model rarely needs it, so the cost of AI review is spent only where it earns its keep.
>
> Complete: **Phase 6.4 — an independent judge for style.** An `openevals` LLM-as-judge scores each converted file 1–5 on a written rubric (user-facing locators, web-first assertions, POM/test shape) and was calibrated before use: all 12 goldens scored 5, all 24 deliberately broken goldens scored lower, and 24 of 24 repeat pairs agreed, with two different judge models. Scoring the six saved experiments showed what the exact gates cannot: Haiku files that compile, lint, and keep parity but still port `By.id` as CSS and return text for tests to assert on (3/5), and Opus first drafts that the internal critic rejected but both judges rated near-perfect (4.9 and 4.6) — reflection lifted Haiku's style and slightly lowered Opus's. Two judges agreed exactly on 81% of rows and within one point on 100%. [Report](docs/phase-6.4-report.md) · [theory + calibration](docs/evaluation-judge.md) · gap [T11](docs/gap-log.md): the Anthropic judge lost ~40% of replies to a provider-side cut; GPT-5.4 lost none.
>
> Complete: **Phase 6.5 — does the repair loop earn its extra calls?** The 6.3 A/B (one attempt vs up to three; only the attempt cap changes) was run per actor with the same Opus critic, on the same 12 pinned files, all arms verified in LangSmith.
>
> ![One attempt vs reflection per actor, same Opus critic](docs/reflection-shootout.svg)
>
> **Haiku:** 2/12 → 9/12 fully passed, 8 files repaired, every compile failure fixed, ×1.9 cost. **Sonnet:** 11/12 → 10/12, nothing static to fix, one critic-variance swing. **Opus:** 6/12 → 11/12, 2 real repairs, 4 variance rows. Reflection earns its cost when first drafts are often wrong; when they are already clean it mostly re-argues the critic, and the Opus critic is the bill. Sonnet alone matches Opus-with-reflection at about half the price. [One-page explanation](docs/reflection-shootout.md); numbers in [reflection-shootout-table.md](docs/reflection-shootout-table.md); walkthroughs: [Haiku](docs/reflection-haiku-ab.md), [Opus](docs/reflection-ab.md); reports: [Haiku](docs/phase-6.5-haiku-report.md), [Sonnet](docs/phase-6.5-sonnet-report.md), [Opus](docs/phase-6.3-report.md). All 251 offline tests pass.
>
> Complete: **Phase 7.1 — a conversion is now a conversation.** A SQLite checkpointer gives every run a `thread_id`, so a second turn can be one sentence: `--thread login --refine "use getByTestId for every form field"` — no file path, no previous output, nothing re-pasted. Standing instructions accumulate on the thread and travel with *every* later model call, including repair laps, so the loop can't quietly undo your convention; the critic sees them too, so it reviews against your rule instead of flagging it. In the live two-turn run the agent applied the rule to the three fields that had ids and **refused to invent one** for the submit button that had none — keeping the faithful CSS locator with a `TODO(review)` saying why. Your instruction wins on style, never on truth. [Walkthrough + live diff](docs/short-term-memory.md). [Session restart notes](docs/session-handoff.md).
>
> Complete: **Phase 7.2 — the agent asks before it guesses.** Four gates and a critic catch code that is *wrong*; nothing catches code that is merely *not what you meant*. A deterministic detector flags the three Selenium patterns with more than one correct Playwright translation — browser dialogs, injected `executeScript`, and a session shared across tests by a `before` hook — and the graph calls LangGraph's `interrupt()` to **suspend itself mid-run** and ask, before spending a token on a guess. Your answer resumes the thread (`Command(resume=...)`), goes to the actor *and* the critic, and is remembered for every later turn. Live proof: the same alerts page converted twice, same model, differing only in the answer — `handler-first` produced `page.once("dialog", ...)` registered before the click, `expect-event` produced `Promise.all([waitForEvent("dialog"), click()])`; 30 lines differ, both passed 4/4 gates and the critic, and the first needed a repair lap because a synchronous dialog handler cannot await. [Walkthrough + live diff](docs/human-in-the-loop.md).
>
> Complete: **Phase 7.3 — teach it once, not once per conversation.** A thread remembers what you said in *that* conversation; the next file starts blank. So preferences now live in a LangGraph **store** of their own — keyed by user, not by thread — and each run recalls only the few that matter for the file in front of it, ranked by an embeddings model against a distilled profile of that file. Live proof: a rule taught while converting a page object on one thread (*"in test specs, wrap each action in a named test.step() block"*) was applied by itself two conversations later to an upload spec, in a fresh thread, with nothing typed — 25 lines different from the identical control run, both passing four gates and the critic on the first attempt. Three other remembered preferences correctly stayed behind, including one about the CI pipeline. The recall threshold was **measured, not guessed** ([calibration](docs/long-term-memory.md#5-calibrating-the-number-nobody-can-guess)): the naive "paste the file in" query separated nothing, and the biggest single factor turned out to be how a preference is *worded*. [Walkthrough + live diff](docs/long-term-memory.md). **Phase 7 complete.**
>
> Complete: **Phase 8.1 — the agent has a front door.** Everything above was reachable only as `python -m selenium2playwright.graph <file> --sixteen --flags`, with the whole front end living at the bottom of the graph module. It is now `s2p`, a real command on your PATH: `s2p convert <file>`, plus `s2p remember`, `s2p memories`, `s2p forget` and `s2p threads` — because teaching the agent a preference was never a kind of conversion. Typer builds the parser from the function signature (so `--max-attempts 9` is refused before a file is opened), and rich prints the result as a scorecard — the four gates, the critic, and the open-TODO count in one grid, with the reason on its own line — followed by a syntax-highlighted before/after diff. On a refine turn that diff is against the *previous turn*, not the source. `graph.py` lost 300 lines and is now only the graph. stdout is still exactly the converted file, so `s2p convert x.ts > x.spec.ts` still works. [Walkthrough](docs/cli.md).
>
> Complete: **Phase 8.2 — pick the model when you run it, and get the answer as data.** The model used to be a fact about the machine (`S2P_MODEL` in `.env`, the same until you edited the file). Now it is a fact about the *run*: `s2p convert page.ts --model opus`, or `--model haiku --critic-model opus` for a cheap actor with a strong reviewer — aliases for convenience, any `provider:model` string accepted, and a named model whose key is missing is refused before the first token instead of after a minute. The mechanism matters more than the flag: these settings travel as LangGraph **context**, the per-invocation channel, not as graph state — so on turn 2 of a saved thread the flag you just typed wins over what turn 1 recorded, which is the opposite of what putting it in state would have done. `--json` puts the whole outcome on stdout as one versioned document — scorecard, findings, ledger, token counts, the models that ran, and the converted code inside it — so a refusal is still something a caller can parse. Verified beyond Anthropic, too: the same graph converts with an OpenAI actor and an Anthropic critic in one run (4/4 gates + critic pass), a machine whose only key is OpenAI is told the one line that fixes it *before* the run instead of failing on authentication halfway through, a provider whose package is missing tells you the one line to add, and the critic's one Anthropic-specific output setting moved out of the graph so any provider works. [Walkthrough](docs/config.md). **Phase 8 complete.**
>
> Complete: **Phase 9.1 — read the whole folder before converting any of it.** Suite mode starts here, and it starts with no model at all. `s2p scan samples/selenium-suite` walks the tree, decides what each file *is* — page object, test, plain helper, or something outside the MVP — and prints the **order**: a topological sort of the import graph, shown as *waves*. The sample suite comes back as two waves, six page objects then the six specs that import them, because a spec converted before its page object is a spec written against something that does not exist yet. The point of a wave rather than a flat order is that nothing inside one depends on anything else inside it — which is the licence step 9.2 needs to fan the whole wave out in parallel with LangGraph's `Send`. The scan also splits a distinction single-file mode never had to make: a data helper with no browser code in it gets `unsupported` from the classifier and **copy** from the scanner, because in a folder "nothing to convert" is not a refusal. Cycles, files that leave the folder, and already-Playwright files are all named out loud with their reason instead of being dropped. `--json` emits the manifest as one versioned document; same folder in, byte-identical plan out. [Walkthrough](docs/suite-scan.md).
>
> 🗺️ **[Interactive architecture diagram](https://claude.ai/code/artifact/877b27e1-3cc2-4f84-802f-091419bf27c1)** — the whole system on one page: the pipeline, the reflection loop, memory, evals, and the v2 AgentCore path. *(Source: [docs/architecture.html](docs/architecture.html))*

## The thesis

Migrating a Selenium suite to Playwright is mechanical enough to automate, but risky enough that "an LLM rewrote it" isn't good enough. So this agent is built on one rule: **the model can't lie to the compiler.** Every conversion must pass deterministic gates — `tsc --noEmit`, typed ESLint, a Selenium-residue scan, and structure parity (the converted suite keeps the same test cases and assertion coverage as the original) — and a critic loop repairs what fails, re-running up to 3 times before anything reaches you. Whatever can't be verified ships as an explicit `TODO(review)`, never silently.

## The graph today

Generated from the compiled graph with `build_graph().get_graph().draw_mermaid()`. Dotted edges choose the next node: intake reviews or refuses; the critic requests another conversion or assembly. A failed conversion also goes to assembly, preserving any earlier draft. The three-attempt limit is enforced by the graph's routing. `recall` fetches the handful of preferences you have taught it that matter for this file; `risk_review` is where the graph stops to ask you about a pattern with more than one correct conversion — it suspends the run rather than guessing.

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	intake(intake)
	recall(recall)
	risk_review(risk_review)
	convert(convert)
	refuse(refuse)
	validate(validate)
	critic(critic)
	assemble(assemble)
	__end__([<p>__end__</p>]):::last
	__start__ --> intake;
	convert -.-> assemble;
	convert -.-> validate;
	critic -.-> assemble;
	critic -.-> convert;
	intake -.-> recall;
	intake -.-> refuse;
	recall --> risk_review;
	risk_review --> convert;
	validate --> critic;
	assemble --> __end__;
	refuse --> __end__;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```

Try it: `uv run s2p convert samples/selenium-suite/pages/LoginPage.ts` prints a scorecard and a before/after diff to stderr and the converted TypeScript to stdout (`--out <file>` writes it instead; `--no-diff` drops the diff). Add `--thread <id>` to save the conversation, then refine it later with `s2p convert --thread <id> --refine "…"` and nothing else; a threaded run also pauses to ask about risky patterns (`--answer dialogs=auto-dismiss` answers up front, `--no-ask` never asks). `s2p remember "…"` files a preference that applies to *every* future conversation (`s2p memories` lists them, `s2p forget <key>` drops one, `--no-recall` ignores them); `s2p threads` lists saved conversations. `--model opus` (or `haiku`, `sonnet`, `fable`, or any `provider:model`) picks the model for that run, `--critic-model` the reviewer, and `--json` returns the whole outcome — code included — as one document on stdout. A run permits up to three conversion and three critic invocations. Exit codes: 0 = all gates and critic pass with no open TODOs, 1 = `needs-review`, 2 = unsupported input or invalid CLI arguments. See the [CLI walkthrough](docs/cli.md), the [companion-file example](docs/validation-node.md#run-a-conversion), or run the [seeded reflection demo](docs/reflection-loop.md#live-demo).

## Why an agent — and not just Claude in a repo?

Fair question: Claude in a chat can convert a Selenium file. The difference is what you can trust *unattended*:

| | Claude in a chat / repo | This agent |
|---|---|---|
| **Verification** | you review everything by hand | output must pass compile, lint, residue and parity gates; a critic loop repairs failures (up to 3 passes) *before you see the code* |
| **Parity** | test cases or assertions can silently vanish in translation | test count and assertion coverage are checked against the source suite — a mismatch triggers self-correction, never a silent drop |
| **Quality** | depends on that day's prompt — vibes | scored on a fixed eval dataset (compile-pass %, residue rate, judge score) with a CI gate against regressions |
| **Scale** | file-by-file babysitting | whole suites: page objects first, then tests, converted in parallel |
| **Reusability** | requires prompting skill | CLI + playground — same result for anyone, including a CI pipeline |

For a one-off file, Claude in a repo is genuinely fine. An agent earns its existence when the job is **repeated, large, or needs guarantees** — and closing the gap from "the model can do it in chat" to "a system you can trust unattended" is exactly the engineering this project demonstrates.

## What's coming

- [x] Architecture + phased roadmap
- [x] **M0** — one-prompt conversion, traced end-to-end in LangSmith
- [x] **M1** — LangGraph pipeline: classify → convert (with honest refusals)
- [x] **M2** — deterministic validators + reflection loop
- [x] **Evals** — 12 pinned files, four exact gates + a calibrated LLM judge. Sonnet, one attempt: 12/12 static, 11/12 graph, judge 4.4/5 at $0.29; Haiku needs the repair loop (2→9/12); Opus first drafts judge 4.9/5. Details in [phase-6.4-report.md](docs/phase-6.4-report.md) and [reflection-shootout.md](docs/reflection-shootout.md)
- [x] **M3** — conversation memory ([threads + checkpointer](docs/short-term-memory.md)) + [human-in-the-loop for risky patterns](docs/human-in-the-loop.md) + [long-term memory across conversations](docs/long-term-memory.md)
- [x] **M3.5** — the `s2p` command line: [convert, scorecard, diff](docs/cli.md) + [runtime `--model` and `--json`](docs/config.md)
- [ ] **M4** — whole-suite conversion: page objects first, then tests, in parallel
- [ ] **M5** — deployed playground you can try

**Stack:** Python · LangGraph · LangSmith · any LangChain chat model — Claude by default, OpenAI verified end to end, swappable per run with `--model` or in `.env` via `S2P_MODEL` · TypeScript toolchain as the referee

---

*Built in public by [Varun Bhatt](https://github.com/varunbhatt2193).*
