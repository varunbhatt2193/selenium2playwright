"""Free the graph when abandoned runs wedge it. Read-only unless told otherwise.

    uv run python deploy/fly/unwedge.py list
    uv run python deploy/fly/unwedge.py interrupt <run_id> [<run_id> ...]
    uv run python deploy/fly/unwedge.py interrupt --all
    ...then:  fly machine restart <id> -a s2p

Why this exists (2026-09-12): two suite runs started two minutes apart on
2026-09-11 wedged the graph — CPU idle, no model calls, nothing advancing.
A machine restart alone never fixes it: the runs stay `running` in Postgres,
and about two minutes after every restart the sweeper re-claims them
(`source=sweep_abandoned`), the workers wedge again, and from then on every
authenticated API request hangs before it is even logged. The page shows
"the backend did not answer in time". The SDK's `runs.cancel` cannot help,
because the API it talks to is the thing that is hung.

So the remedy is in the database: mark the runs `interrupted` (the sweeper
only re-claims `running`), set their threads `idle`, and restart.

It opens its own tunnel — `fly proxy 15432:5432 -a s2p-postgres` — unless
one is already listening. The password comes from deploy/fly/.pgpassword
(gitignored, written by deploy.sh) and is never printed.
"""
from __future__ import annotations

import pathlib
import socket
import subprocess
import sys
import time

import psycopg

HERE = pathlib.Path(__file__).resolve().parent
PORT = 15432
PG_APP = "s2p-postgres"


def listening(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def main(argv: list[str]) -> int:
    mode = argv[0] if argv else "list"
    if mode not in {"list", "interrupt"}:
        print(__doc__)
        return 2
    password = (HERE / ".pgpassword").read_text().strip()
    uri = (f"postgres://postgres:{password}@127.0.0.1:{PORT}/postgres"
           "?sslmode=disable&connect_timeout=10")

    proxy = None
    if not listening(PORT):
        proxy = subprocess.Popen(["fly", "proxy", f"{PORT}:5432", "-a", PG_APP],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(30):
            if listening(PORT):
                break
            time.sleep(1)
        else:
            print(f"fly proxy never opened 127.0.0.1:{PORT}")
            proxy.terminate()
            return 1
    try:
        with psycopg.connect(uri, autocommit=True) as conn:
            if mode == "interrupt":
                ids = argv[1:]
                if ids == ["--all"]:
                    cur = conn.execute("SELECT run_id FROM run WHERE status = 'running'")
                    ids = [str(r[0]) for r in cur.fetchall()]
                if not ids:
                    print("nothing to interrupt")
                else:
                    cur = conn.execute(
                        "UPDATE run SET status = 'interrupted', updated_at = now() "
                        "WHERE run_id = ANY(%s::uuid[]) AND status = 'running' "
                        "RETURNING run_id, thread_id", (ids,))
                    done = cur.fetchall()
                    print("interrupted:", [str(r) for r, _ in done])
                    if done:
                        cur = conn.execute(
                            "UPDATE thread SET status = 'idle', updated_at = now() "
                            "WHERE thread_id = ANY(%s::uuid[]) AND status = 'busy' "
                            "RETURNING thread_id", ([str(t) for _, t in done],))
                        print("threads set idle:", [str(t) for (t,) in cur.fetchall()])
            cur = conn.execute("SELECT status, count(*) FROM run GROUP BY status ORDER BY 1")
            print("runs by status:", dict(cur.fetchall()))
            cur = conn.execute(
                "SELECT run_id, thread_id, status, created_at, updated_at, "
                "kwargs->'config'->'metadata'->>'owner' FROM run "
                "WHERE status IN ('running', 'pending') ORDER BY created_at")
            rows = cur.fetchall()
            print(f"{len(rows)} run(s) running or pending" + (":" if rows else "."))
            for r in rows:
                print("  ", *[str(x)[:36] for x in r])
            if rows and mode == "list":
                print("Next: interrupt them (ids above, or --all), then "
                      "`fly machine restart <id> -a s2p`.")
    finally:
        if proxy is not None:
            proxy.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
