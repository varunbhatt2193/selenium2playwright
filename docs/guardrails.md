# Step 10.4 — making a public URL safe to hand out

Step 10.2 put the converter on the internet at `s2p.fly.dev`. It answered
everybody:

```
$ curl -X POST https://s2p.fly.dev/threads
HTTP 200
```

No token, no limit, no record. That is fine for a private experiment and it is
not a link you put on a CV, for three separate reasons that are easy to run
together and are worth keeping apart.

## The three problems, which are not the same problem

**Money.** Fly's bill is a constant — three fixed machines, no autoscaler, about
$19.50 a month whether nobody visits or ten thousand people do. The machines are
already paid for. What is not paid for is the model spend behind them: every
conversion is real Anthropic tokens on a real card, and an endpoint anybody can
POST to is an endpoint anybody can spend from.

**Files.** The convert graph takes `source_path` and, when no `source_text` came
with it, *reads that path on the server*. That is exactly right when the server
is your own laptop — it is how the CLI works. On a public host it is file
disclosure:

```json
{"assistant_id": "convert", "input": {"source_path": "/etc/passwd"}}
```

The suite graph is worse: its entire input is server-side directories, so
`{"root": "/"}` is a request to walk the disk.

**Everyone else's memory.** Long-term memory is *shared*. One visitor sending
`remember: "always use xpath"` would quietly degrade every later conversion, for
everybody, with nothing on fire and no way to notice.

Only the first is about cost. The second is a security bug. The third is a
correctness bug that would look like the model getting worse.

## Authentication and authorization are two questions

LangGraph Platform separates them, and the separation is the useful part:

```
authentication   who is this?        @auth.authenticate   once per request
authorization    may they do THAT?   @auth.on...          per resource and action
```

The first returns an identity. The second gets that identity *plus the thing
being attempted* and answers yes, no, or "yes, but only your own rows". That
split is what lets one visitor be allowed to convert a file and refused
permission to read the file next to it.

`langgraph.json` names the `auth` object:

```json
"auth": { "path": "./src/selenium2playwright/guard.py:auth" }
```

The CLI turns that into `LANGGRAPH_AUTH` in the image and the server imports it
at start-up. Nothing about the graph changes.

### Two keys

`S2P_API_KEY` is you — every graph, no limits, the store included.
`S2P_DEMO_KEY` is the playground — metered, inline text only, its own memories.
`deploy/fly/deploy.sh` generates both into `.env` if they are missing, so a
deployment cannot be born without them.

With *neither* set the server refuses every request rather than serving an open
one, and says which variable to set. An unconfigured deployment that silently
ran open is the precise failure this step exists to prevent, so "unconfigured"
had to mean closed. Local development opts out explicitly with `S2P_AUTH=off`.

### What a visitor may send

Refused, by field, each with the reason in the 403 body:

| field | why |
| --- | --- |
| `context_paths` | reads files on the server |
| `repo_paths`, `caller_paths`, `pending_paths` | read files on the server; the suite graph builds them itself from a `source_tree` |
| `output_path` | writes a file on the server |
| `remember` | writes to shared long-term memory |
| `root`, `out_root` | the suite graph's server-side directories |
| `max_attempts` > 3 | one request becoming ninety-nine model calls |

`user_id` is overwritten with the caller's own identity rather than validated,
because there is no value a visitor could send that we would want to honour.

`source_path` is the interesting one, and the first version got it wrong. It is
two things wearing one name: without `source_text` it is a path the server
opens; *with* `source_text` it is only a label — the classifier, the recall
query and the report all want to know the file is called `LoginPage.ts`, and
none of them care where it lives. Refusing the field outright took a name away
from every visitor to close a hole that only exists in the other case. So the
rule is on the shape: a bare filename, no separators, no `..`, no leading dot.

## The meter

Three limits, deliberately different in shape:

```
burst    3 per 60s, per visitor    stops one person hammering
daily    10 per day, per visitor   stops one person grinding
budget   $5/day, everyone          stops the BILL, whoever spends it
```

Only the third is about money; the first two are about fairness between
ordinary visitors. The budget is set in **dollars**, because that is the unit
the card is billed in, and enforced in **runs**, because a run must be allowed
or refused before the model answers and the price is only known afterwards.
`S2P_COST_PER_RUN` bridges the two and is a deliberate overestimate — the
ceiling should bind early rather than late.

Counts live in **Postgres**, which the deployment already runs for the
checkpointer. One `INSERT ... ON CONFLICT DO UPDATE ... RETURNING count` is as
atomic as `INCRBY`, so two requests arriving at the same instant on two workers
cannot both see "9 of 10 used". Postgres has no `EXPIRE`, so each row carries an
`expires_at` and a row past its deadline reads as absent and is replaced in
place by the next bump; a sweep deletes long-dead rows as housekeeping only.

They lived in Redis until 2026-09-08, and Redis was the wrong shelf. The
deployment starts it with `--appendonly no` and no volume on purpose —
`redis.toml` says it holds "in-flight run state", and losing that costs only
whatever was mid-flight. The budget counter is not in-flight state. A routine
redeploy restarted Redis mid-afternoon and set the day's spend back from 22 to
0, which made the ceiling $5 *per deploy* rather than per day, on the one limit
that exists to stop the bill. `/limits` now reports `counts_in` and
`survives_restart` so the same failure would be visible rather than silent.

With no usable `POSTGRES_URI` this falls back to Redis, and with neither to a
dictionary in this process. Falling back is a downgrade in durability, never in
enforcement, and it says which shelf it chose.

Two refunds, for opposite reasons, and the asymmetry is the design:

- A run refused by the **burst** limit keeps its count. If refusals were free, a
  tight retry loop would slip through the window that exists to close it.
- A run refused by a **spend** limit gives its count back. Those counters mean
  "runs that actually started", and a refused run starts nothing. Charging for
  it would walk the ceiling down every time somebody bounced off it.

**Fail closed.** If Redis is unreachable we cannot know what has been spent, and
"we cannot know" is not a reason to allow spending. The owner is still let
through, because the owner is the person who has to go and fix it.

At 80% of the day's budget an alert fires once — logged, and POSTed to
`S2P_ALERT_WEBHOOK` if one is set. Not a refusal: crossing 80% on a busy day is
normal, worth knowing about, and not worth breaking anything over.

## The flywheel

The Phase 6 eval dataset is a guess about which files are hard. Real visitors
converting real files are a better guess, and the moment one says "this came out
wrong" is the most valuable signal this project can collect: a labelled failure,
for free, on an input nobody thought to try.

```
POST /feedback  {"run_id": ..., "score": 0, "source_text": ..., "comment": ...}
```

A 👍 is recorded in LangSmith. A 👎 is recorded *and* the input is queued as an
example in the `s2p-feedback-queue` dataset — tagged, unreviewed, and with **no
expected output**, because an example with a golden answer nobody has written
would be worse than none at all: the eval would start scoring against a blank. A
human triages the row, writes the golden, and only then does it graduate into
the eval set.

The queue happens even when attaching the score to the run fails. Attaching a
score is bookkeeping; the input somebody said we got wrong is the artifact worth
having, and it does not stop being worth having because LangSmith could not find
a run id.

## Two routes the platform does not have

`langgraph.json` also names a Starlette app:

```json
"http": { "app": "./src/selenium2playwright/http_app.py:app" }
```

`GET /limits` exists so the playground can be honest *before* wasting anybody's
time. Without it, the only way to discover the demo is out of budget is to
submit a file, wait, and be refused — which reads as "this is broken" rather
than "this is busy".

`POST /feedback` is where the button lands.

## Five things that were wrong, and how each was found

None of these came from reading the code.

**Custom routes are not covered by the auth middleware.** The platform's
authenticator guards *its* API — assistants, threads, runs, store — and does not
run for routes merged in through `http.app`. `POST /feedback` answered anybody.
The failure is silent: the route simply works, for everyone. Found by asking the
running server for it with no credentials. The routes now call
`guard.identify()` themselves.

**A synchronous client in an async handler blocks the event loop.** LangSmith's
`Client` is synchronous, and calling it directly from a route stalls every other
request on that worker until LangSmith answers. `langgraph dev` detects it and
raises — *"Blocking call to socket.socket.connect"* — which is the only reason
this was caught; in production it would have been a server that gets slow under
load for no visible reason. Now `asyncio.to_thread`.

**Route docstrings are parsed as OpenAPI YAML.** A line containing
`` `stored: false` `` is read as a mapping and the server fails at start-up with
a `ScannerError` that names the file and not the reason.

**`source_path` as a label.** Described above: the first version refused it
outright and broke the ordinary case to close the dangerous one.

**`langgraph-sdk` sends `x-api-key`, not `Authorization`.** The guard read only
the bearer header, so the project's own client — the thing that calls this
deployment more than anything else — was refused with *"Missing bearer token"*.
Both spellings are accepted now: the SDK's, and the `Authorization: Bearer` that
`curl` and the playground reach for.

## Proving it

`tests/test_guardrails.py` — 27 offline tests calling the handlers directly. No
Redis, no LangSmith, no model. They prove the logic and prove nothing about
whether the server ever calls it.

```
uv run python scripts/check_guardrails.py --url https://s2p.fly.dev
```

That is the other half: real HTTP, real keys, against whatever is actually
running. It would have caught the middleware gap, and did.

Nothing in it spends a model call, and that is not luck. Every refusal it
provokes is decided in front of the graph. The one probe that must be *accepted*
to prove the throttle works sends a payload over the 256 KB cap, so the refuse
node turns it away without asking a model anything — proving a rate limit should
not cost the thing the rate limit protects.

```
Anonymous callers
  ✓ GET /ok is open (Fly's health check)              200
  ✓ GET /limits needs a token                         401
  ✓ POST /feedback needs a token                      401
  ✓ POST /threads needs a token                       401
  ✓ a wrong token is refused                          401

The server's filesystem
  ✓ a visitor may open a thread                       200
  ✓ source_path cannot read a server file             403
  ✓ context_paths cannot read server files            403
  ✓ output_path cannot write a server file            403
  ✓ the suite graph is closed to visitors             403
  ✓ shared memory cannot be written                   403
  ✓ max_attempts cannot be inflated                   403

The meter
  ✓ GET /limits answers a demo token                  200
  ✓ a visitor is throttled before the budget is       True
     accepted 3 run(s), then 429

The flywheel
  ✓ POST /feedback accepts a thumbs-down              200
  ✓ a thumbs-down is queued even so                   True
```

## What this does not do

**Keep-warm needs nothing.** Fly's health check already GETs `/ok` every 30
seconds, and `auto_stop_machines = false` means the machines never sleep. A
second pinger would be a cron job that proves the first one runs. Saying so is
more honest than shipping one.

**Online evals on sampled traffic are Phase 11.** The signal they need — real
feedback on real traffic, tagged — is what this step produces. Configuring the
evaluators is a LangSmith-side job that wants a few days of actual rows first.

**None of this makes the demo free.** The budget is a ceiling, not a refund. A
day where every one of the day's runs is used is a day that costs about $5, and
$5 × 30 is a real number. `deploy/fly/teardown.sh --yes` is still the off
switch.
