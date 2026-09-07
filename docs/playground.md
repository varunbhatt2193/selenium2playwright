# Step 10.3 — the playground: a paste box in front of the agent

Step 10.2 put the converter on the internet. Step 10.4 made the URL safe to hand
out. Neither made it *visible*: `https://s2p.fly.dev` is a JSON API, and a person
who opens it in a browser sees

```json
{"detail":"Not Found"}
```

Everything the last four phases built — the four gates, the reflection loop, the
critic, the consolidated TODO ledger — is real and completely invisible. This
step is the difference between "there is an agent deployed" and "here, try it".

![The playground converting LoginPage.ts against the live deployment](playground.jpg)

```bash
uv run --group ui streamlit run ui/app.py
```

Two files, and the split between them is the first thing worth understanding.

| file | what it is | tested by |
|---|---|---|
| `ui/app.py` | the layout: what goes where on the page | your eyes |
| `src/selenium2playwright/playground.py` | every decision the page makes | `tests/test_playground.py`, 57 tests |

## Streamlit in five minutes, because the model explains the code

Streamlit is a way to write a web page as a Python script. There is no HTML, no
callbacks-and-state framework, no JavaScript. You write:

```python
name = st.text_input("Your name")
if st.button("Greet"):
    st.write(f"Hello {name}")
```

and you get a text box and a button. The part that surprises everybody, and the
part that shapes this whole step, is **what happens when you click**:

> Streamlit re-runs the entire script, top to bottom, on every single
> interaction.

Not a callback. Not a diff. The whole file, from line 1, every time. `st.button`
returns `True` on exactly the run that follows its click and `False` on every
other run. That model has three consequences here:

**1. Anything you want to keep must live in `st.session_state`.** Ordinary
variables are born and die on each run. `st.session_state` is a dictionary that
survives, per browser tab. That is where the last result, the thread id, the run
id and the visitor id live.

**2. A file that *is* the program cannot be unit-tested.** Importing `ui/app.py`
runs the app. So `ui/app.py` holds no logic at all: building the request,
reading the scorecard, formatting the diff, explaining an HTTP failure — all of
it is in `playground.py`, which is ordinary Python. One of the tests asserts
that `playground.py` never imports Streamlit, because the moment it does, none
of the others can be written.

**3. A widget's value is not committed the instant you type it.** `st.text_input`
sends its value when it loses focus or you press Enter. Type a comment, then
click the button next to it, and the script sees the *previous* value — nothing,
the first time. See "The two bugs only running it could find" below; the fix is
`st.form`, which submits every widget inside it together.

## What the page does

**Six sample buttons**, each a real file from `samples/selenium-suite`, with a
tooltip saying why that one is interesting (`AlertsPage.ts`: native dialogs, which
have no Playwright twin). A paste box in front of a stranger is a blank page; the
buttons are so somebody who has never written Selenium can still watch the thing
work in one click.

**A paste box and a file name.** The name is a *label*, not a path — see the
guardrail section.

**Live progress while it runs.** A conversion takes 30–90 seconds, and the graph
streams the name of each node as it finishes. Those names are translated
(`validate` → "Running the four gates: compile, residue, lint, parity") and, from
the second lap on, numbered ("Converting to Playwright — attempt 2"), because
watching the reflection loop go round is the most interesting thing on the page
and "convert, convert, convert" looks like a stuck progress bar.

**The scorecard**: status, attempt count, the four gates as PASS/FAIL, the
critic's verdict, and which models did the work. `needs-review` is shown as a
real outcome, not a failure.

**Three tabs**: the Playwright file (with a Download button), a unified diff
against what you pasted, and Review — the consolidated TODO(review) ledger, the
model's notes, and any errors.

**Ask for a change.** A second turn on the same thread: "Keep search() returning
the number of results, using count()." The graph still has the last draft and
every instruction given so far (step 7.1's short-term memory), so a refinement is
one sentence rather than a re-paste.

**👍 / 👎.** The score attaches to the run in LangSmith. A 👎 also sends the file
back, which is what `feedback.py` queues into the `s2p-feedback-queue` dataset —
the score is a number on a chart, but *the input somebody says we got wrong* is a
dataset row.

**A budget line in the sidebar**, read from `GET /limits` before anything is
submitted: "31 of 41 conversions left today ($5/day)." A demo that says how much
is left is a demo that looks maintained; the alternative is discovering the
budget by waiting a minute and being refused.

## The playground is a *visitor*, and that is the whole security story

It authenticates with `S2P_DEMO_KEY`, never `S2P_API_KEY`. The owner key bypasses
the meter, so a playground holding one would run the day's budget straight past
every guardrail step 10.4 built.

The key lives in the environment of the machine running Streamlit and never
reaches the browser. A person using the playground gets a URL, not a credential.

Everything the demo identity may not do, the playground simply never offers:

| step 10.4 refuses | the playground |
|---|---|
| `source_path` as a path | sends `source_text`; the name is a label, checked against the same bare-filename rule |
| `context_paths` | sends `context_text` — the companion's actual bytes |
| `output_path`, `remember`, suite runs | no such control exists on the page |
| `max_attempts` above 3 | never sent |
| files over 256 KB | refused under the text box, in KB, before the round trip |

Those client-side checks are not the playground being careful on its own
initiative — the server enforces all of it anyway. They exist so that a rule
arrives as a sentence under the input while it can still be fixed, instead of as
a 403 after the click. `tests/test_playground.py` runs the real `guard.guard_run`
handler over the request this module builds, which is the only test in the suite
that would notice the two halves drifting apart.

## Talking to the deployment

```python
client = get_sync_client(url=..., api_key=demo_key(), headers={"X-S2P-Visitor": visitor})
for chunk in client.runs.stream(thread_id, "convert", input=request,
                                stream_mode=["updates", "values"]):
```

`X-S2P-Visitor` is a random id per browser session. The per-visitor limits (3 a
minute, 10 a day) are meant to count *people*, and without this header every
visitor would look like one caller — the playground's own server address — and
the third person to arrive would be rate-limited by the first two.

The two stream modes are two views of the same run: `updates` names the node that
just finished (the progress line), `values` is the whole state after it (the
answer, once the last one arrives). A third event, `metadata`, arrives first and
carries the **run id** — which is how a 👎 clicked ninety seconds later knows
which run it is talking about.

`GET /limits` and `POST /feedback` are not part of the graph API, so they are
called with plain `httpx` and a `Bearer` token. They authenticate themselves
(`http_app.py`), which is the fifth bug of step 10.4 and worth re-reading.

## The three things only running it could find

**1. A spec pasted alone cannot compile, and it is nobody's fault.** The first
live run of `login.spec.ts` came back `needs-review` with `compile=FAIL`: the
spec imports `../pages/LoginPage`, and the server has never seen that file. The
finding was *correct* — and it was about the paste box, not about the
conversion, which is the worst kind of first impression. The fix is the
**companion file** box: an already-converted Playwright page object, sent as
`context_text`, exactly what the suite graph feeds every file in wave 2. The
`login.spec.ts` button now pre-fills it from `samples/playwright-golden`, and the
same run came back **passed, 4/4 gates, critic pass**.

**2. A typed comment was silently dropped.** The 👎 comment box and the 👎 button
were separate widgets, so a visitor who typed "the locator is wrong" and clicked
👎 without pressing Enter sent an *empty* comment — losing the only part of the
feedback with any detail in it, with no error and no way to notice. Both the
feedback box and the refine box are now `st.form`s, where every widget submits
together. Verified by typing a comment, not pressing Enter, clicking 👎, and
reading the row back out of LangSmith:

```
2026-09-07 23:34  LoginPage.ts | playground form check: comment typed without
                  pressing Enter | pg-73c5ad1bd1a4 | unreviewed
```

**3. The budget line lied by one.** The sidebar is drawn at the top of the
script, before the conversion the click starts — so after a run it still showed
the number from before it. A `st.rerun()` when the conversion finishes fixes it,
and gives the node trail somewhere permanent to live ("What it did — 10 steps"),
since the live status box belongs to the run that drew it.

A fourth, found while writing the tests rather than by running it:
`feedback.pending()` — the triage call — always returned an empty queue from a
shell, because `feedback.py` never loaded `.env` and so never had a LangSmith key
to build a client with. Inside the deployment the variable is already in the
environment, which is exactly why nobody noticed.

## Configuration

| variable | what it does |
|---|---|
| `LANGGRAPH_DEPLOYMENT_URL` | which backend to talk to; defaults to `http://127.0.0.1:8123` (a local `langgraph up`) |
| `S2P_DEMO_KEY` | the key the playground calls with. Falls back to `S2P_API_KEY` only for a local server running `S2P_AUTH=off` |

Both are already in `.env`, so `uv run --group ui streamlit run ui/app.py` talks
to the live deployment with no arguments. Point `LANGGRAPH_DEPLOYMENT_URL` at
`http://127.0.0.1:8123` to develop against a container on your desk instead;
nothing else changes, which is the same property `scripts/call_deployment.py`
has.

Streamlit is in an opt-in dependency group (`ui`), not in the project's
dependencies. `s2p convert` and the graph must never need a web framework
installed to convert a file.

## Live evidence, 2026-09-07, against <https://s2p.fly.dev>

| what | result |
|---|---|
| `LoginPage.ts` (sample button) | **passed**, 2 attempts, 4/4 gates + critic pass, no TODOs, `getByLabel("Username")` |
| `login.spec.ts` **without** its companion | needs-review, `compile=FAIL` on the unresolvable import — the finding that produced the companion box |
| `login.spec.ts` **with** the companion | **passed**, 2 attempts, 4/4 gates + critic pass |
| `AlertsPage.ts` | **passed**, 1 attempt, 4/4 + critic pass |
| a pasted `SearchPage.ts` (not a sample) | needs-review, 2 attempts, 4/4 + critic pass, one honest locator TODO |
| refine: "Keep search() returning the number of results, using count()" | second turn on the same thread → `async search(term): Promise<number> { … return this.results.count(); }`, 3 attempts, 4/4 + critic pass |
| 👎 with a comment | attached to the run **and** queued into `s2p-feedback-queue` with the comment and visitor id |
| the meter | budget line moved 38 → 31 across the session, one per conversion |

## Where to look next

- [guardrails.md](guardrails.md) — the contract this page is the client half of.
- [deploy/fly/README.md](../deploy/fly/README.md) — the backend it talks to.
- [human-in-the-loop.md](human-in-the-loop.md) and
  [short-term-memory.md](short-term-memory.md) — what "Ask for a change" is
  driving underneath.
