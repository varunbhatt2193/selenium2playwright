# Prevention backlog — after the 2026-09-12 outage

What would have stopped the 2026-09-11 → 09-12 outage, or shortened it from a
day to a minute, in priority order. Written so any of it can be picked up cold.
The incident itself is in `docs/session-handoff.md` ("2026-09-12 — the outage
was a wedge, not a streaming regression"); the remedy is `deploy/fly/unwedge.py`.

**The failure, in one line:** two suite runs in flight at once wedged the graph
workers; the runs stayed `running` in Postgres; every restart was undone two
minutes later when the sweeper re-claimed them; every authenticated request then
hung while `/ok` stayed green — for 22 hours.

| # | Item | Removes | Size | Status |
|---|------|---------|------|--------|
| 1 | Refuse a second concurrent suite | the trigger | ½ day | open |
| 2 | Deep health check on the real path | the 22 hours of green | 2 h | open |
| 3 | Reap stale runs at startup + `BG_JOB_TIMEOUT_SECS` | "restart makes it worse" | 2 h | open |
| 4 | Fail fast on the page | the 5-minute wait | 1 h | open |
| 5 | External probe that opens an issue | finding out by accident | 1 h | open |
| 6 | Pin the API image version | silent upgrades | — | **done** `913786d` |
| 7 | Runbook + script in the repo | losing the remedy again | — | **done** `913786d` |

Do 1 and 3 first: 1 removes the cause, 3 makes a plain restart a safe fix for
whatever the next cause is. 2 and 5 are detection; 4 is manners.

## 1. Refuse a second concurrent suite

**Where:** `src/selenium2playwright/guard.py`, `guard_run`, when the run's
assistant is `suite`. **How:** a "suite in flight" row in the same Postgres
counter table `limits._Counter` already uses (atomic insert, `expires_at` =
now + 15 min, longer than any suite run measured). Acquire it in `guard_run`;
release it in the suite graph's `finish` node (and let the TTL release it if
the run dies). When taken, raise 429 with `Retry-After` = seconds to expiry and
a detail the page can show verbatim: *"Another suite is converting right now.
Try again in about N minutes."* Owner keys are exempt like the other limits.
**Tests:** `tests/test_guard.py` with the fake counter; one test that the
second call gets 429 and the first does not, one that expiry frees it.
**Why not concurrency limits instead:** the gRPC client pool is 5, round-robin,
hard-coded in `langgraph_api/grpc/client.py` (checked 0.14.0) — nothing to tune.
**Done when:** two suite uploads back-to-back on the page give one run and one
polite refusal, and `unwedge.py list` shows one running.

## 2. Deep health check on the real path

**Where:** `src/selenium2playwright/http_app.py`, new route `GET /ok/deep`.
**How:** call the runtime's own `POST /threads/search` on `127.0.0.1:8000`
with the owner key from the environment, wrapped in `asyncio.wait_for(…, 5)`;
`SELECT 1` on Postgres the same way. Any timeout → 503 with which leg failed.
Then a second `[[http_service.checks]]` in `deploy/fly/app.toml` with path
`/ok/deep`, `interval = "30s"`, `timeout = "10s"`, `grace_period = "90s"`.
**Caveat:** a failed Fly check stops routing and shows red; it does not restart
the machine by itself. Pair with 3 (so a restart is safe) and 5 (so someone
hears). **Done when:** `fly checks list -a s2p` shows two checks and, during a
deliberate wedge (two suites, with item 1 disabled), the deep one goes red
within a minute while `/ok` stays green.

## 3. Reap stale runs at startup, and bound a job's life

**Where:** the user lifespan in `http_app.py` (the runtime honours it: the log
says `Entered lifespan context user_router.lifespan`). **How:** at startup,
*before* the sweeper's first pass at about two minutes, and every five minutes
after: `UPDATE run SET status='interrupted' WHERE status='running' AND
created_at < now() - interval '1 hour'`, then the matching threads to `idle`.
A suite takes under ten minutes, so anything older is an orphan whose visitor
left. Log what was reaped. **Also:** `BG_JOB_TIMEOUT_SECS = "1800"` in
`deploy/fly/app.toml` `[env]` — the runtime's own per-job timeout, default
86 400 s (24 h), which is why a wedged run could hold for a day. **Tests:** the
reaper against a fake connection: reaps the old, leaves the young.
**Done when:** with two rows planted at `running` and `created_at` 2 h ago,
`fly machine restart` alone brings the API back and the rows read
`interrupted`.

## 4. Fail fast on the page

**Where:** `ui/server.py` `convert_events` and the suite equivalent;
`src/selenium2playwright/playground.py` `client()` / `TIMEOUT`. **How:** the
first call, `threads.create()`, gets its own 10-second budget (a second SDK
client with a short timeout, or run it on a thread and wait 10 s); on timeout
yield `{"kind": "error", "message": "The converter is busy right now. Try again
in a minute."}` and log it. The 300-second read timeout stays for the stream
itself. **Tests:** `tests/test_web.py` with a fake client whose `create`
sleeps. **Done when:** with the graph wedged (or its URL pointed at a black
hole), the page shows the message within 15 s instead of 5 min.

## 5. External probe that opens an issue

**Where:** `.github/workflows/probe.yml`, `schedule: cron: "*/15 * * * *"`.
**How:** curl `https://s2p.fly.dev/ok/deep` (item 2) and
`https://varun-s2p.fly.dev/api/limits`; on any non-200, `gh issue create`
with label `outage`, skipped if an open `outage` issue exists; close it on the
next green run. Free on a public repo. **Done when:** flipping the graph URL
to a dead host for one cycle opens an issue and the next cycle closes it.

## 6. Pin the API image version — done

`langgraph.json` has `"api_version": "0.14.0"`; the generated Dockerfile
reads `FROM langchain/langgraph-api:0.14.0-py3.12`. The lockfile's
`langgraph-api` only governs local `langgraph dev`. Bump both on purpose,
never by rebuild.

## 7. Runbook and script — done

`deploy/fly/unwedge.py list | interrupt --all`, then `fly machine restart
<id> -a s2p`, then one real conversion through the page before calling it
fixed. The 2026-09-11 scratchpad copy of this script was lost with the
scratchpad; this one is tracked.

## Still unknown: why two suites wedge

Three occurrences (2026-09-08 twice, 2026-09-11), all with two suite runs in
flight, on langgraph-api 0.13.4 and 0.14.0 alike — so not the upgrade. Shape:
CPU idle, no model calls after the first fan-out, nothing advancing, API
requests hanging before the access log line. Hypothesis: a deadlock between the
Python workers and the Go core over the 5-client gRPC pool during two
simultaneous `Send` fan-outs. **Next time it happens, before touching
anything:** tail `fly logs -a s2p` live into a file (the `--no-tail` form is a
~100-line sample), run `unwedge.py list` and keep the output, and note the
exact minute. Then fix. Item 1 makes this moot for the demo; the mechanism
still matters for anyone running suites on their own clone.
