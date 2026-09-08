# Selenium2Playwright

**An AI agent that rewrites TypeScript Selenium test suites in Playwright, and proves the result compiles before you see it.**

[![CI](https://github.com/varunbhatt2193/selenium2playwright/actions/workflows/ci.yml/badge.svg)](https://github.com/varunbhatt2193/selenium2playwright/actions/workflows/ci.yml)

## ▶ Try it — [varun-s2p.fly.dev](https://varun-s2p.fly.dev)

No signup, nothing to install. Paste one file, or drop a zip of your whole Selenium folder and get a Playwright folder back.

[![The playground: Selenium in, Playwright out, four gates and a critic in between](docs/playground.jpg)](https://varun-s2p.fly.dev)

<table>
<tr><th>You give it Selenium</th><th>You get back Playwright</th></tr>
<tr>
<td>

```ts
await driver.findElement(By.id('email')).sendKeys('me@example.com');
await driver.findElement(By.css('button[type=submit]')).click();
await driver.wait(until.urlContains('/home'), 5000);
```

</td>
<td>

```ts
await page.locator('#email').fill('me@example.com');
await page.locator('button[type=submit]').click();
await expect(page).toHaveURL(/\/home/);
```

</td>
</tr>
</table>

## What makes it an agent, not a prompt

- **The model can't lie to the compiler.** Every conversion must pass four deterministic gates: `tsc --noEmit`, typed ESLint, a Selenium-residue scan, and structure parity (same test cases, same assertion coverage as the source).
- **It fixes its own work.** An AI critic reviews the output and the graph repairs what failed, up to three attempts, before anything reaches you. Whatever can't be verified ships as an explicit `TODO(review)`, never silently.
- **It converts whole suites.** Page objects first, then the specs that import them, in parallel, and then the delivered tree is compiled as one project with a parity ledger you can hand over.
- **It asks before it guesses.** Three Selenium patterns have more than one correct Playwright translation (dialogs, `executeScript`, a session shared by a `before` hook). The run suspends and asks you, then remembers the answer.
- **It learns your conventions.** Tell it once ("wrap each action in a named `test.step()`") and it applies that rule in later conversations, on the files where it matters.

## Measured, not claimed

| What was measured | Result |
|---|---|
| The 12-file sample suite, converted as one upload | **12/12 passed**, tree compiles as one project, about 20 seconds |
| Does self-correction earn its cost? (12 pinned files, same critic) | Haiku **2/12 → 9/12**; Opus **6/12 → 11/12**; Sonnet 11/12 first try |
| Style, scored by a calibrated LLM judge | Two judges agreed within one point on **100%** of rows |
| The twelve SDET hard cases, as a second benchmark | **6/11 → 9/11** after two playbook rules, with the delta proven from the code |
| Converted tests run in a real browser, in CI, with no model spend | **20/20** test rows pass when the page object interface is supplied |
| Offline test suite | **632 tests**, on every push, no secrets and no tokens |

Full numbers and how they were produced: [Evaluation primer](docs/evaluation-primer.md) · [live evaluation page](https://varun-s2p.fly.dev/evaluation) · [what is still unsolved](docs/hard-cases.md).

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

## How it works

```
intake → recall → risk_review → convert → validate → critic → assemble
                                   ▲                      │
                                   └── repair (≤ 3) ──────┘
```

A LangGraph state machine. `intake` classifies the file or refuses it honestly. `recall` fetches the few remembered preferences that apply. `risk_review` pauses the run on a pattern with more than one right answer. `convert` writes Playwright, `validate` runs the four gates, `critic` reviews, and the graph loops back with the actual findings until it passes or the attempt cap is hit. `assemble` always reports the outcome and keeps the latest draft. Suite mode wraps the same graph in a scan, a parallel fan-out per wave, and a whole-tree compile.

**Stack:** Python · LangGraph · LangSmith · any LangChain chat model (Claude by default, OpenAI verified end to end, swappable per run with `--model`) · pinned TypeScript toolchain as the referee · React + Vite playground over FastAPI, self-hosted on Fly behind auth and a dollar budget.

[Interactive architecture diagram](https://claude.ai/code/artifact/877b27e1-3cc2-4f84-802f-091419bf27c1) · [Architecture and decisions](plan.md) · [Build log, phase by phase](docs/build-log.md)

## Run it yourself

```sh
uv sync
cp .env.example .env            # add ANTHROPIC_API_KEY (or OPENAI_API_KEY + S2P_MODEL=openai:gpt-5.4)
uv run s2p convert samples/selenium-suite/pages/LoginPage.ts
uv run s2p suite samples/selenium-suite --out out/suite
```

`s2p convert` prints a scorecard and a before/after diff, and writes the converted TypeScript to stdout. `s2p suite` converts a folder and writes the report beside it. Add `--model haiku --critic-model opus` for a cheap actor with a strong reviewer, `--json` for the whole outcome as one document, or `uv run langgraph dev` to step through a run in LangGraph Studio. [CLI walkthrough](docs/cli.md) · [playground walkthrough](docs/playground.md) · [deployment](docs/deploy.md).

---

*Built in public by [Varun Bhatt](https://github.com/varunbhatt2193). The demo is metered at 15 conversions per visitor per day against a shared $5/day budget, because every conversion is real tokens on a real card.*
