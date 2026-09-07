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

Roughly **$20/month** with everything always-on: app $11.11, Postgres $5.92 plus
$1.50 of volume, Redis ~$1.94.

## Run it

```bash
fly auth login              # opens a browser
./deploy/fly/deploy.sh      # from the repository root
```

Safe to re-run — it is also the redeploy command. Existing apps, volumes and
the database password are reused rather than recreated.

Then:

```bash
curl https://s2p.fly.dev/ok
uv run python scripts/call_deployment.py samples/selenium/LoginPage.ts --url https://s2p.fly.dev
```

Put `LANGGRAPH_DEPLOYMENT_URL=https://s2p.fly.dev` in `.env` to make that the
default and drop the `--url`.

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
