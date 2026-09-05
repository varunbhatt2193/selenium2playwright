# Prompt v1 gap log — one-shot conversion vs. the golden

*Roadmap 2.1, run 2026-09-04 on `claude-sonnet-5`, prompt = role line + `docs/playbook.md`.
Draft written by Claude from the raw diffs; Varun reviews, edits, and owns the list.
Step 2.3 grows this into the failure taxonomy that drives the validators (Phase 4).*

## Setup

| Run | Input | Output vs. golden |
|---|---|---|
| POM | `selenium-suite/pages/LoginPage.ts` | semantically identical; 3 cosmetic diffs (below) |
| Test, with converted POM as context | `tests/login.spec.ts` + `--context out/2.1/pages/LoginPage.ts` | correct code, but wrapped in a markdown fence |
| Test, no context | `tests/login.spec.ts` alone | byte-identical to the golden except the final newline |

Every run read ~2.1k tokens of playbook from the prompt cache (≈85 % of input at cache price).

## Gaps found

1. **Markdown fences leak into the output** (test-with-context run) despite the
   system prompt saying "no markdown fences". Writing that text to `.ts` would
   fail `tsc` on line 1. → *Fix owner: 2.2 structured output* — a schema field
   `code` cannot contain a fence by accident the way free text can.
2. **Output is not deterministic in shape.** Same file, two runs: one fenced,
   one clean. Anything downstream must not assume the "good" shape.
   → *2.2 (schema) + 4.x validators*: never trust, always check.
3. **No trailing newline at EOF** in all three outputs. Harmless to `tsc`,
   flagged by ESLint `eol-last` / Prettier. → *Normalize on write* (2.2) and
   let the lint gate (4.3) enforce it.
4. **Value imports where the golden uses type-only imports**:
   `import { Page, Locator }` vs. `import { type Locator, type Page }`. Both
   compile under the current sandbox `tsconfig.json`; under
   `verbatimModuleSyntax` the value form is an error. → *Playbook candidate*:
   "import `Page`/`Locator` as `type`". Decide when the sandbox config is
   frozen in 4.1.
5. **Gratuitous identifier rename**: private field `url` became `path`. Not
   wrong, but unrequested drift makes diffs noisier and review harder.
   → *Playbook candidate*: "preserve identifiers unless a rule requires a
   change (e.g. rule 20 turning a getter into a Locator field)".
6. **Formatting drift only** (one-line vs. wrapped `expect(...)` calls). Pure
   Prettier. → An exact-match eval would be wrong here; the 6.x evaluators
   must format both sides (or compare AST) before diffing.
7. **The test converted correctly without seeing the converted POM** because
   playbook rule 20 made `flashMessage` predictable. That is luck with a
   rule behind it, not a guarantee — a POM with a less obvious converted API
   would be guessed. → Suite mode (9.x) keeps the POM-first wave plan; the
   `--context` flag in `one_shot.py` is the single-file preview of it.

## What did *not* go wrong (worth recording)

- Every test name and assertion survived (parity held, rules 2 / 24).
- All waits, `Builder`, `driver.quit()`, `this.timeout` were deleted, not
  translated (rules 4–6, 12–14).
- Locators climbed the ladder to `getByLabel` / `getByRole` (rule 7) and
  `getFlashText()` became an exposed `Locator` + web-first assert (rule 20).
- `baseURL` relative path (rule 22).
