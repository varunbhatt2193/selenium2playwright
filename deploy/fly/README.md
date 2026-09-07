# Self-hosting the converter on Fly.io

The managed LangSmith platform could not host this image — see
[docs/deploy.md](../../docs/deploy.md) §9 for the full anatomy. This directory
runs the *same image* somewhere that can.

Nothing here works around a bug. The image's entrypoint runs its bundled Go
core in-process whenever `CORE_API_GRPC_SIDECAR` is unset, which is everywhere
except that one platform, so the topology below is simply the normal one.

## What gets created

```
s2p            our image, 2 GB, public HTTPS   ← the only thing on the internet
  │ .internal (IPv6 private network)
  ├── s2p-postgres    pgvector/pgvector:pg16 + 10 GB volume
  └── s2p-redis       redis:6
```

Roughly **$19.50/month** with everything always-on: app $11.11, Postgres $5.92
plus $0.45 of volume, Redis ~$1.94.

## Run it

```bash
fly auth login              # opens a browser
./deploy/fly/deploy.sh      # from the repository root
```

Fly requires a card on the organization before it will create any app, even one
that would fit inside a free allowance.

Safe to re-run — it is also the redeploy command. Existing apps, volumes and
the database password are reused rather than recreated.

Then:

```bash
curl https://s2p.fly.dev/ok
uv run python scripts/call_deployment.py samples/selenium-suite/pages/LoginPage.ts --url https://s2p.fly.dev
```

Put `LANGGRAPH_DEPLOYMENT_URL=https://s2p.fly.dev` in `.env` to make that the
default and drop the `--url`.

## Cost control

**Fly has no spending cap and no billing alerts.** Their own cost-management
page says it: *"We don't support billing alerts (yet), so budget accordingly"*,
and of the free allowance, *"there's no soft ceiling. If you go over, we'll
bill you."* So there is no switch to flip.

Prepaid credit looks like the workaround and is not one. Fly support:
*"Credits are not a way to control spend... any remaining unpaid amount is
charged to your payment method on file"* and *"We aren't able to cap spending
limits at this time."* When the balance hits zero the account rolls onto the
card rather than stopping. Nor can the card be removed — Fly requires one on
file for "deploying multiple apps and deploying public images", which is this
project exactly. A virtual card with a bank-side monthly limit is the only hard
ceiling that actually exists; it lives at the bank, not at Fly, and a decline
suspends the apps.

What we do on our side is make the bill a constant. Nothing in this directory
scales: one machine per app, fixed sizes, `--ha=false` on every deploy, no
autoscaler, no per-request pricing. Traffic does not move the number. The only
ways the bill can change are a deploy that creates a machine we did not intend,
or a volume someone grows.

Two commands hold that line:

```bash
./deploy/fly/cost.sh            # what is running vs. what should be
./deploy/fly/teardown.sh --yes  # destroy all three apps, stop the meter
```

`cost.sh` prints every machine and volume the project owns next to the expected
shape, so drift shows up in one glance. It deliberately does not guess at
rates — the authoritative month-to-date figure is on the Fly dashboard, and the
script's last line links there.

`teardown.sh` is the actual limit. A stopped machine still bills its volume;
only destroying the app stops the charge, so tearing down and re-running
`deploy.sh` is a normal thing to do between demos rather than a last resort.
Re-creating takes one command and a few minutes of build.

The Postgres volume is 3 GB, not 10. Volumes can be extended later and never
shrunk, so the small end is the reversible one — `fly volumes extend` when
threads and memories actually need the room.

## What it took to get right, first time through

Recorded because none of it is guessable and all of it cost a round trip.

**Secrets are forwarded from `.env` wholesale, not from a list.** The first
version named the keys it expected. It missed `ANTHROPIC_WORKSPACE_ID`, so the
container started clean, authenticated fine, and then took `400 Bad Request`
from Anthropic on every model call — an identity-linked key must name the
workspace it acts in, and `llm.py` sends it as the `anthropic-workspace-id`
header. A hand-maintained list fails silently and late, which is the worst way
for a deployment to fail. It now forwards every key in `.env` and skips only
the handful the deployment defines for itself.

**`set -o pipefail` makes ordinary shell idioms fatal.** `tr </dev/urandom |
head -c 32` returns 141: `head` closes the pipe, `tr` dies of SIGPIPE. The
password generator killed the script before step 2. Every early-closing
pipeline here is now a captured variable, and the password comes from `openssl`.

**`fly apps list` prints a box-drawn table that indents names by one space,**
so `^name`-anchored matching never fires and "does this app exist?" answers no
forever. The checks read `--json`.

**A `[[services]]` block is a request for public ingress.** Postgres and Redis
had one, purely to hold `auto_stop_machines = false`, and Fly duly allocated
public v4 and v6 addresses for a database. Private networking needs no services
block at all — any listening port answers at `<app>.internal` over 6PN — and
without one there is no public address to leave lying around. Autostop is a
property of services too, so removing the block removes the only thing that
could have stopped those machines.

## Why a self-run Postgres instead of Fly's managed one

The server's migrations create **three** extensions — `vector`, `ltree` and
`btree_gin` — each guarded like this:

```sql
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'ltree') THEN
    CREATE EXTENSION ltree;
  END IF;
END $$;
```

The guard means a *pre-installed* extension costs no privilege at all. But
creating one needs superuser, and Fly's Managed Postgres offers only `vector`
and PostGIS, enabled by a dashboard toggle. It would clear `vector` and then
fail on `ltree` — the same class of failure that ended the LangSmith attempt.
Running the container ourselves makes us superuser and all three succeed.

That is also why `store.index` is back in `langgraph.json` here: semantic recall
needs pgvector, and on our own database we can have it.

## Two things that will bite if changed

**Bind to IPv6, not loopback.** Fly's private network is IPv6. `redis.toml`
passes `--bind ::` because redis defaults to `127.0.0.1`, where it would answer
nothing from `s2p-redis.internal` and the API would fail its startup wait with
no useful error.

**Do not let the machines scale to zero.** A conversion runs for minutes and
holds state while it works. `auto_stop_machines = false` on all three; the API
keeps `auto_start_machines = true` so a cold visitor still wakes it.

## Secrets

`deploy.sh` reads the keys out of `.env` once and hands them to `fly secrets`,
which stores them outside the repository and outside any image layer — the same
reason `.dockerignore` excludes `.env`. They are never printed. The generated
Postgres password lands in `deploy/fly/.pgpassword` (gitignored) because
`POSTGRES_URI` has to be reassembled from it on a redeploy.

To rotate a key: `fly secrets set -a s2p ANTHROPIC_API_KEY=...` — the app
restarts with it. Rotating the database password means updating both the
Postgres app's secret and the API app's `POSTGRES_URI` together.
