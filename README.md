# 🚧 Selenium2Playwright — work in progress

**An AI agent that converts TypeScript Selenium test suites to Playwright** — a single test, a page object, or the whole suite — built on LangGraph + Claude, with a self-correcting loop that validates its own output before you ever see it.

> **Status: building in public.** Phase 5 of 12 implemented — **bounded reflection is working**. The graph converts, validates, and reviews code, then repairs it using the actual findings, for at most three conversion attempts. Assembly always reports the outcome and retains the latest available draft. A live seeded demo repaired a missing `await` on attempt 2: all four gates and the critic passed, while two locator TODOs correctly kept the final status at `needs-review`. See the [reflection walkthrough and demo](docs/reflection-loop.md).
> Architecture & decisions: [plan.md](plan.md)
>
> 🗺️ **[Interactive architecture diagram](https://claude.ai/code/artifact/877b27e1-3cc2-4f84-802f-091419bf27c1)** — the whole system on one page: the pipeline, the reflection loop, memory, evals, and the v2 AgentCore path. *(Source: [docs/architecture.html](docs/architecture.html))*

## The thesis

Migrating a Selenium suite to Playwright is mechanical enough to automate, but risky enough that "an LLM rewrote it" isn't good enough. So this agent is built on one rule: **the model can't lie to the compiler.** Every conversion must pass deterministic gates — `tsc --noEmit`, typed ESLint, a Selenium-residue scan, and structure parity (the converted suite keeps the same test cases and assertion coverage as the original) — and a critic loop repairs what fails, re-running up to 3 times before anything reaches you. Whatever can't be verified ships as an explicit `TODO(review)`, never silently.

## The graph today

Generated from the compiled graph with `build_graph().get_graph().draw_mermaid()`. Dotted edges choose the next node: intake converts or refuses; the critic requests another conversion or assembly. A failed conversion also goes to assembly, preserving any earlier draft. The three-attempt limit is enforced by the graph's routing.

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	intake(intake)
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
	intake -.-> convert;
	intake -.-> refuse;
	validate --> critic;
	assemble --> __end__;
	refuse --> __end__;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```

Try it: `uv run python -m selenium2playwright.graph samples/selenium-suite/pages/LoginPage.ts` prints the latest converted TypeScript to stdout and the final report to stderr. A run permits up to three conversion and three critic invocations. Exit codes: 0 = all gates and critic pass with no open TODOs, 1 = `needs-review`, 2 = unsupported input or invalid CLI arguments. See the [companion-file example](docs/validation-node.md#run-a-conversion) or run the [seeded reflection demo](docs/reflection-loop.md#live-demo).

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
- [ ] **M2** — deterministic validators + reflection loop
- [ ] **Evals** — measured conversion quality on a public dataset *(the numbers will go here)*
- [ ] **M3** — conversation memory + human-in-the-loop for risky patterns
- [ ] **M4** — whole-suite conversion: page objects first, then tests, in parallel
- [ ] **M5** — deployed playground you can try

**Stack:** Python · LangGraph · LangSmith · any LangChain chat model (Claude by default, swappable via `S2P_MODEL`) · TypeScript toolchain as the referee

---

*Built in public by [Varun Bhatt](https://github.com/varunbhatt2193).*
