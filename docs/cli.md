# Step 8.1 — `s2p`: giving the agent a front door

Everything the agent could do since Phase 0 was reachable only as
`uv run python -m selenium2playwright.graph <file> --flag …`. It worked, and
nobody outside this repo would ever type it. Phase 8 turns the agent into a
command: `s2p convert`, `s2p remember`, `s2p threads` — installed on the PATH by
`uv sync`, with a scorecard you can read at a glance and a before/after diff.

This is a surface change. No node, no prompt, and no gate changed, and the four
gates plus the critic still decide the outcome; the whole of 8.1 is *how a
person drives it and what they see back*.

---

## 1. The problem: the front end lived in the agent's basement

`graph.py` was 758 lines, and about 300 of them were argparse and `print()`.
Each new capability made that worse — 7.1 added `--thread` and `--refine`, 7.2
added `--answer` and `--no-ask`, 7.3 added `--remember`, `--user`, `--memories`,
`--forget`, `--no-recall`, `--memory-db`. Three of those are not conversions at
all: `--memories` and `--forget` are *management*, bolted onto a command whose
first argument is a file to convert, so they had to be special-cased
("teaching, listing or forgetting on their own: no file to convert") before the
graph was even built.

Two problems in one: a module that was both the agent and its UI, and a UI
whose shape (one command, sixteen flags) no longer matched what it did.

## 2. What moved where

| | before | after |
|---|---|---|
| the graph | `graph.py`, 758 lines | `graph.py`, 448 lines — nodes, edges, routing, nothing else |
| the surface | argparse + `print()` at the bottom of `graph.py` | `cli.py` — Typer commands, rich rendering |
| the entry point | `python -m selenium2playwright.graph` | `s2p`, from `[project.scripts]` in `pyproject.toml` |
| management | `--memories`, `--forget`, a bare `--remember` | `s2p memories`, `s2p forget <key>`, `s2p remember "…"` |

`graph.py` now has no `main()` and no `if __name__ == "__main__"`. There is one
front end, and `cli.py` is it — which is also what Phase 9 (suite mode) and
Phase 10 (the deployed server) need: another caller of the same graph, not
another copy of the same argument parsing.

## 3. Typer in one paragraph

**Typer builds the parser from the function signature.** A parameter without a
default is a positional argument; one with a default is an option named after
it (`max_attempts` → `--max-attempts`); the type annotation is both the
converter and the validator; the docstring is the command's help text. Anything
argparse would need a keyword for — a metavar, a short flag, a range — is
attached to the parameter with `Annotated[type, typer.Option(...)]`, so the
declaration stays in one place instead of being split between a signature and a
matching `add_argument` call.

```python
max_attempts: Annotated[int, typer.Option(
    "--max-attempts", min=1, max=MAX_ATTEMPTS,
    help="total conversion attempts: 1 = no repairs; 3 = draft plus two repairs")] = MAX_ATTEMPTS
```

`--max-attempts 9` is now rejected by the parser with *"9 is not in the range
1<=x<=3"*, before a file is opened or a model is built. Each `@app.command()`
function is a subcommand, which is what finally lets teaching and listing stop
pretending to be conversions.

Underneath, Typer is Click, and Click ends every command by raising
`SystemExit` — including on success. That is fine for a person and useless for
a test, so `cli.run(argv)` catches it and returns the number instead. The
console script `s2p` points at the Typer app directly; everything in `tests/`
goes through `run()`.

## 4. rich: a scorecard, not a wall of PASS lines

The old output was one line per gate. The new one is a table, printed by rich:

```
Conversion: needs-review (2/3 attempts) — Validation and the critic passed, but TODO(review) items still need a human.
samples/selenium-suite/pages/LoginPage.ts → Playwright
┏━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━┓
┃ check        ┃ result ┃ detail             ┃
┡━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━┩
│ compile      │ PASS   │ clean              │
│ residue      │ PASS   │ clean              │
│ lint         │ PASS   │ clean              │
│ parity       │ PASS   │ clean              │
│ critic       │ PASS   │ no fixes requested │
│ TODO(review) │ 3      │ needs a human      │
└──────────────┴────────┴────────────────────┘
```

Three deliberate choices in that picture:

- **The critic is a row, not a headline.** The four gates above it are facts a
  compiler produced; the critic is a model's opinion. Same table, same weight,
  no special framing.
- **The open TODO count is a row too**, because it is the reason this run is
  `needs-review` while every check says PASS. That combination confused people
  reading earlier phases' output; now the cause is in the grid.
- **The verdict and the reason are a plain line above the table**, not a cell.
  Reasons are sentences, and a sentence in a cell gets folded into a column;
  outside the table it is soft-wrapped and never cropped.

Then the before/after, as a syntax-highlighted unified diff:

```
╭─ LoginPage.ts → converted ───────────────────────────────────────────────────╮
│ -import { By, WebDriver, until } from "selenium-webdriver";                   │
│ +import { Page, Locator } from "@playwright/test";                            │
│ …                                                                             │
│ -    await this.driver.findElement(this.usernameInput).sendKeys(username);    │
│ +    await this.usernameInput.fill(username);                                 │
╰───────────────────────────────────────────────────────────────────────────────╯
```

Selenium to Playwright is close to a whole-file rewrite, so this diff is long by
nature — that is the honest picture, and `--no-diff` turns it off. On a
**refine turn** the "before" is the previous turn's output instead of the
source, because what changed *this turn* is the question you are actually
asking; the panel title says which one you are looking at.

## 5. Two rules the layout keeps

**stdout is the file; stderr is for you.** Every table, panel, note and warning
goes to stderr, so `s2p convert x.ts > x.spec.ts` still writes usable
TypeScript and nothing else. `--out` writes the file directly and stdout stays
empty. This is unchanged from Phase 2 and it is why the rich output could be
added without breaking a single pipe.

**Nothing is silent.** A preference that exists but was not close enough to
apply, a risky pattern nobody was asked about, a critic that could not be
reached: each is said out loud, with the number or the reason. The scorecard
made this easier to keep, not harder — a fact with no row is a fact somebody
has to go looking for.

## 6. Live proof

```console
$ uv run s2p convert samples/selenium-suite/pages/LoginPage.ts --out out/8.1/pages/LoginPage.ts
[selenium · none · typescript] selenium-webdriver page object / helper in TypeScript
Conversion: needs-review (2/3 attempts) — Validation and the critic passed, but TODO(review) items still need a human.
… scorecard …
  note: Rule 22: absolute URL replaced with relative path; baseURL expected in playwright.config.ts.
  TODO(review): locator inferred from selenium selector By.id("username"); not confirmed against
  actual page markup that the field has an accessible label "Username".
Conversion tokens (all attempts): [7979 in / 1748 out · cache write 2690 · cache read 2690]
Critic tokens (all attempts): [9408 in / 1736 out · cache write 3301 · cache read 3301]
… diff …
[wrote out/8.1/pages/LoginPage.ts]
$ echo $?
1
```

Real Sonnet actor and critic, two attempts, all four gates and the critic
passing, exit **1** — because the model inferred three locators from `By.id`
selectors it could not verify against the live page and said so, in the code and
in the ledger. That is the behaviour every earlier phase already had; 8.1 is the
first time you can see all of it without scrolling.

## 7. Four sharp edges

1. **rich reads `[...]` as markup.** A note saying *"kept the `[data-testid]`
   locators"* would have had `[data-testid]` swallowed as a style tag. Every
   line that carries model output, tool output or a path goes through `say()`,
   which builds a `rich.text.Text` — literal by construction. A test pins it.
2. **Click always exits by raising.** Even a successful command raises
   `SystemExit(0)`. `cli.run()` is the seam that turns that back into a return
   value, and it is why the test suite reads `code, out, err = run_cli(...)`.
3. **A passing gate can still have findings.** ESLint warnings are reported,
   not fatal. The detail column says `1 finding(s)` next to a `PASS`, rather
   than `clean`, and the finding itself is printed under the table — hiding it
   behind the verdict would lose it.
4. **Tests assert on rows, not on sentences.** The old assertions matched
   `"PASS compile"`; in a table the gate and its verdict are separated by box
   characters, so the tests now match the *line* (`compile[^\n]*PASS`) and the
   helper that finds a row filters on the box character so the prose line
   mentioning "critic" can never be mistaken for the critic row.

## 8. What this is not

- **Not configurable, in 8.1.** `--model`, `--critic-model` and `--json` arrived
  in step 8.2, through the graph's context schema — see [config.md](config.md).
  In 8.1 the model still came from `S2P_MODEL` in `.env`.
- **Not a suite runner.** One file (plus already-converted companions) per
  invocation. Walking a directory in waves is Phase 9 — step 9.1 added a sixth
  subcommand, `s2p scan <folder>`, which reads a suite and prints the conversion
  plan without calling a model; see [suite-scan.md](suite-scan.md). Converting
  that plan is 9.2 and 9.3.
- **Not a new agent.** Same graph, same prompts, same four gates, same critic.
  Every eval and every earlier phase's numbers still stand, because nothing
  below `cli.py` changed.

## 9. Five questions worth asking

1. Why is `s2p memories` a subcommand now, when `--memories` worked?
2. Why does the reason for a verdict sit outside the table instead of in it?
3. What breaks if `say()` is replaced with `console.print()`?
4. On turn 2 of a thread, what is the diff comparing — and why not the source?
5. What has to exist below `cli.py` before `s2p convert --model opus` can work? ([answer](config.md))
