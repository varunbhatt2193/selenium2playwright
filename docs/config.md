# Step 8.2 — choosing the model at run time: context, not environment

Until now the model was a fact about the *machine*: `S2P_MODEL` in `.env`,
read at import time, the same for every run until you edited the file. That is
fine for one developer with one habit. It is useless for "convert this one
tricky file with Opus", impossible for a server that answers two requests with
two different models, and it makes a comparison ("does Opus fix what Sonnet
misses here?") a matter of editing a file between runs and hoping you remember
to put it back.

Step 8.2 adds three flags — `--model`, `--critic-model`, `--json` — and, more
importantly, the mechanism under them: LangGraph's **context schema**, the
supported way to hand a graph the settings for *one* invocation.

```
uv run s2p convert page.ts --model opus
uv run s2p convert page.ts --model haiku --critic-model opus   # cheap actor, strong reviewer
uv run s2p convert page.ts --json > outcome.json               # the whole result as data
```

---

## 1. State, context, and why the difference matters

The graph already had two kinds of memory, and this adds a third kind of input.
They are easy to confuse, so:

| | what it holds | how long it lives |
|---|---|---|
| **state** | what this conversation is *about*: the source file, the standing instructions, the last conversion | checkpointed — restored on the next turn of the thread |
| **store** | what the user has taught the agent, across every conversation | a separate database, outlives threads |
| **context** | how *this invocation* runs: which model, how many attempts | the length of one `invoke`; never saved |

Put the model in the state and you get a bug that is hard to see and easy to
ship. State is restored: turn 1 of a thread would record `model = sonnet`, and
turn 2 — typed by a person who has just added `--model opus` — would load turn
1's value on top of the new one and quietly review with Sonnet anyway. Context
is passed fresh on every call and stored nowhere, so **a flag typed on turn 2
is obeyed on turn 2**. That is the whole reason for the distinction, and there
is a test named after it.

## 2. The schema, and the three lines that wire it

A context schema is an ordinary dataclass. Declaring it on the builder is what
makes LangGraph accept `context=` on `invoke` and hand it to nodes:

```python
@dataclass(frozen=True)
class RunSettings:
    model: str = ""             # the actor; "" = whatever .env says
    critic_model: str = ""      # the reviewer; "" = follow the actor
    max_attempts: int | None = None

builder = StateGraph(ConversionState, context_schema=RunSettings)      # accept it
def intake(state, runtime: Runtime[RunSettings] | None = None):        # receive it
    run = settings(runtime)
compiled.invoke(inputs, config=config, context=run)                    # pass it
```

A node opts in by declaring a `runtime` parameter — the same "ask for it by
name" injection the store already uses (`store: Optional[BaseStore]`), and the
same silent failure if you get the name wrong. The `= None` default is
deliberate: it keeps the node a plain function of state, callable directly in a
test, and LangGraph injects it either way.

**One node reads the context: `intake`.** It resolves the models and the lap
budget once, at the top of the turn, and writes the answers into the state:

```python
"max_attempts": resolve_attempt_cap(cap),
"models": env.resolve_roles(run.model, run.critic_model),
```

Then `convert` and `critic` read *that record* rather than the context again.
This is on purpose: the name printed on the scorecard, the name in the JSON,
the name in the trace and the name handed to the provider are then all the same
string, resolved in one place. A model label that can disagree with the model
that ran is worse than no label.

## 3. Who decides which model — the precedence

Four sources, and `env.resolve_roles` is the one function that ranks them:

```
--model opus      →  the flag, expanded through the alias table
(no flag)         →  S2P_MODEL in .env
(neither)         →  DEFAULT_MODEL, anthropic:claude-sonnet-5
```

with one wrinkle for the reviewer: `--model` alone moves **both** the actor and
the critic, because one model for both is the normal arrangement. A split you
asked for deliberately — `--critic-model`, or `S2P_CRITIC_MODEL` in `.env` —
survives a change of actor, so `--model sonnet` on a machine configured for an
Opus critic still gets the Opus critic.

`sonnet | opus | fable | haiku` are a convenience table in `env.py`, not a
restriction: any `provider:model` string is passed through untouched, which is
what keeps the model-agnostic rule intact. A bare word that is *not* an alias
is an error rather than a guess — inferring a provider is how you get an
authentication failure from the wrong company two minutes into a run.

A model named on the command line is also checked before the run starts: known
provider, key present, key shaped like that provider's keys. The message names
the environment variable and never prints the value.

## 4. `--json`: the outcome as data

The human surface is a table, a diff and a verdict on stderr. `--json` adds the
other audience — CI, the suite runner of Phase 9, the server of Phase 10 — by
putting one document on **stdout**:

```json
{
  "schema": "s2p.conversion-report/v1",
  "source": "samples/selenium-suite/pages/LoginPage.ts",
  "output": "out/8.2/LoginPage.ts",
  "status": "needs-review",
  "exit_code": 1,
  "models": { "actor": "anthropic:claude-haiku-4-5-20251001",
              "critic": "anthropic:claude-opus-5" },
  "max_attempts": 3,
  "turn": 1,
  "usage": { "conversion": {...}, "critic": {...} },
  "report": { "attempts": 2, "validation": [...], "critique": {...},
              "result": { "code": "…", "notes": [...], "todos": [...] } }
}
```

Three decisions in that shape. The converted **code is inside the document**
(`report.result.code`), so `--json` alone is a complete answer and nothing has
to be scraped off the human output. The **schema is named and versioned**, so a
consumer can check what it is reading instead of guessing from the keys. And a
**refusal is still a document** — same shape, `report: null`, `status:
"refused"`, `exit_code: 2` — because the outcomes a caller likes least are
exactly the ones it must be able to parse.

Stdout stays single-purpose: with `--json` it is the document, without it the
converted TypeScript, and with `--out` it is the document or nothing, because
the file has already been written. Every exit path goes through one `emit()`
function so that promise is kept in one place rather than at five `return`s.

## 5. Live proof

One file, one command, two different models — neither of them the one in
`.env` (which says `anthropic:claude-sonnet-5` for both roles):

```console
$ uv run s2p convert samples/selenium-suite/pages/LoginPage.ts \
    --model haiku --critic-model opus --out out/8.2/LoginPage.ts --json --no-diff > report.json

[selenium · none · typescript] selenium-webdriver page object / helper in TypeScript
Models: actor anthropic:claude-haiku-4-5-20251001 · critic anthropic:claude-opus-5
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
│ TODO(review) │ 2      │ needs a human      │
└──────────────┴────────┴────────────────────┘
Conversion tokens (all attempts): [6578 in / 1043 out · cache write 0 · cache read 0]
Critic tokens (all attempts): [9092 in / 969 out · cache write 3300 · cache read 3300]
[wrote out/8.2/LoginPage.ts]
$ echo $?
1
```

Haiku drafted, the Opus critic asked for a repair, the second draft passed all
four gates and the review, and the run still exited **1** — because two
`TODO(review)` items about a `baseURL` nobody has configured are honest
uncertainty, not a failure to be smoothed over. That behaviour is unchanged
from 8.1; the two model names on line 2 are the new part.

The "visibly" in *"`--model` visibly changes the traced model"* means from
outside the process, so it was checked in LangSmith rather than on screen —
the root run and its four LLM spans, read back through the SDK:

```
conversion-graph  tags: ['prompt:v1', 'step:8.2', 'attempts:3',
                         'model:claude-haiku-4-5-20251001', 'critic:v1']
   metadata: {'actor_model': 'anthropic:claude-haiku-4-5-20251001',
              'critic_model': 'anthropic:claude-opus-5', 'max_attempts': 3}
   llm: ChatAnthropic | claude-haiku-4-5-20251001     (draft, then repair)
   llm: ChatAnthropic | claude-opus-5                 (review, then review again)
```

Two actor calls on Haiku, two critic calls on Opus, in one run, with `.env`
untouched. And `report.json` on stdout parsed in one line, with the converted
file inside it at `report.result.code`.


## 6. Any provider, not just Anthropic

`--model` and `S2P_MODEL` have always claimed "any LangChain chat model". That
claim was only ever *exercised* on Anthropic for the graph itself, so it was
checked properly and three things needed fixing.

**Every run checks the model it is about to use, not just a named one.** The
commonest case is a fresh machine whose only key belongs to a different
provider than the default, and the old behaviour answered that configuration
question with a *conversion* verdict: the run went through intake, called
Anthropic, failed to authenticate, and exited **1** with an empty scorecard.
Now it stops before any work, exits **2**, and says what this machine can
actually do:

```console
$ s2p convert page.ts          # OpenAI key set, no Anthropic key, nothing configured
Invalid value: anthropic:claude-sonnet-5 needs ANTHROPIC_API_KEY, which is not set
(see .env.example). Keys are set here for openai — run with --model openai:<model>,
or put S2P_MODEL=openai:<model> in .env.
```

**The preflight asks the provider, not a table.** `env.PROVIDER_KEYS` knows the
key variable for fourteen providers (anthropic, openai, azure_openai,
google_genai, groq, mistralai, deepseek, xai, together, fireworks, cohere,
openrouter, perplexity, voyage) and gives a friendly message when one is
missing. A provider it has *not* heard of used to be a hard refusal — "Add it
to PROVIDER_KEYS in env.py" — which made "any model" untrue. Now `env` stays
quiet and `llm.check_model` **builds the client** (local, no network) and lets
the provider answer:

```console
$ s2p convert page.ts --model groq:llama-3.3-70b-versatile
Invalid value: groq:llama-3.3-70b-versatile: Initializing ChatGroq requires the
langchain-groq package. Please install it with `pip install langchain-groq`
 — here: uv add langchain-groq

$ s2p convert page.ts --model bogus:thing
Invalid value: bogus:thing: Unable to infer model provider for model='bogus:thing'.
Write it as provider:model, e.g. openai:gpt-5.4. Any LangChain provider works once
its package is installed (see .env.example).
```

Optional providers are deliberately not dependencies: `langchain-anthropic` and
`langchain-openai` ship with the project, and anything else is one `uv add`
away — the error says which one.

**The critic no longer hard-codes one vendor's trick.** It asked for
`method="json_schema"` on every provider, which is an Anthropic requirement
(forced tool choice fights adaptive thinking). That decision moved into
`llm.structured_kwargs`, which asks for native JSON schema on the providers
where it is known to work and otherwise uses the default tool-calling path
every integration implements. The node is now provider-blind.

**What is still vendor-specific lives in `llm.py` only** — three things: the
Anthropic prompt-cache marker (`prepare_messages`), the critic's `effort` knob,
and that JSON-schema list. Everything else speaks plain LangChain.

Two live runs, on this repo's own samples:

| run | actor | critic | result |
|---|---|---|---|
| `LoginPage.ts` | `openai:gpt-5.4` | `openai:gpt-5.4` | 4/4 gates + critic PASS, 1 attempt, **exit 0** |
| `login.spec.ts` (+ the converted POM) | `openai:gpt-5.4` | `anthropic:claude-sonnet-5` | 4/4 gates + critic PASS, 1 attempt, **exit 0** |
| `LoginPage.ts`, **no Anthropic key in the process at all** | `openai:gpt-5.4` | `openai:gpt-5.4` | 4/4 gates + critic PASS, **exit 1** on four honest locator TODOs |

The second one is the interesting one: **two vendors inside one graph**, the
draft written by OpenAI and reviewed by Anthropic, with the Anthropic critic
still getting its cache read (3,301 tokens) because the cache marker follows the
model that will actually receive the messages, not the run.

The third answers the practical question — *what if I only have an OpenAI key?*
One line, `S2P_MODEL=openai:gpt-5.4` in `.env` (or `--model` per run), and
everything works: the four gates, the critic loop, long-term memory (its
embeddings default to OpenAI anyway), the scorecard and the exit codes. The
model even behaved the way the playbook asks — it refused to invent semantic
locators it could not verify and left four `TODO(review)` items instead, which
is exactly why that run is an honest exit 1 rather than a 0.

What is *not* checked before the run is the model **name** — `openai:gpt-5.9`
builds a client happily and fails on the first call, because finding out costs
a network round trip. And the `--model` aliases are Anthropic-only shorthand;
every other provider is named in full, which is the honest way round for a
project that does not want to curate other people's model lists.

## 7. Sharp edges

1. **A context schema's defaults are not applied for you.** Invoke without
   `context=` and `runtime.context` is `None`, not `RunSettings()` — reading a
   field off it raises `AttributeError`, and every earlier phase, the eval
   runner and most tests invoke exactly that way. Hence `graph.settings()`,
   which every node goes through and which returns the defaults for `None`.
2. **The flag has to beat the restored state, not merge with it.** The lap
   budget can arrive two ways: in the context (the CLI) or as a state input
   (the eval harness, which has passed it that way since 6.3 and whose
   experiment hashes depend on it). Context wins when it is set, because it was
   typed just now; the state input is what a caller with no context uses.
3. **`--json` must own stdout alone.** Printing the code *and* the document
   would leave two things on the same stream and break both readers.
4. **Check the key only when the model was named.** Probing a provider key on
   every run would fail every offline test and every scripted run that never
   calls a provider. The check exists because a *typed* `--model` is a fresh
   claim worth verifying cheaply; a default run's configuration is `env.check`'s
   job.
5. **Aliases are shorthand, not a menu.** `MODEL_ALIASES` is four lines in
   `env.py`; the flag accepts any `provider:model`, so adding a provider is
   `uv add langchain-<provider>` and nothing here.
6. **A key table can only ever be advice.** It cannot know Watsonx's variable
   or your gateway's, so an unlisted provider is unverifiable, not invalid —
   `env.required()` skips it, `env.unverifiable()` reports it, and
   `uv run python -m selenium2playwright.env` prints it as a `•` line rather
   than a `✗`.
7. **A vendor-specific structured-output method is a bug on every other
   vendor.** `method="json_schema"` sat in the critic node for three phases and
   would have failed on any provider without it.

## 8. What this is not

- **Not a config file.** No `s2p.toml`, no profiles. Flags and `.env`, which is
  what a one-file CLI needs; a server will pass the same `RunSettings` per
  request instead.
- **Not a model router.** Nothing chooses the model *for* you based on the
  file. The graph obeys; the human (or the caller) decides.
- **Not a new agent.** Same nodes, same prompts, same four gates, same critic.
  A run with no flags resolves exactly what it resolved yesterday.

## 9. Six questions worth asking

1. Why is the model context and not state — what breaks on turn 2 if you move it?
2. `intake` reads the context, but `convert` reads the state. Why not have both
   read the context?
3. Why does `--model opus` move the critic too, but `S2P_CRITIC_MODEL` survive it?
4. What would `--json` have to change if the code were *not* inside the document?
5. Why is a bare `--model gpt-4o` an error instead of a guessed provider?
6. Why does `check_model` build a client instead of checking a table of key names?
