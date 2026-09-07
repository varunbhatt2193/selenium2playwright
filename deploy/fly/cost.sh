#!/usr/bin/env bash
# What is currently billing, and what it should be billing.
#
#   ./deploy/fly/cost.sh
#
# Fly has no spending cap and no billing alerts — their docs say so outright:
# "We don't support billing alerts (yet), so budget accordingly."
# https://fly.io/docs/about/cost-management/
#
# So the cap is us. This prints every machine and volume the project owns, and
# the shape we expect, so drift is visible in one glance. It does not guess at
# rates: the authoritative month-to-date number is on the dashboard, and the
# last line here takes you to it.
set -euo pipefail

APP="${S2P_FLY_APP:-s2p}"
PY="$(command -v python3)"

show() {
  local A="$1"
  local list; list="$(fly apps list --json 2>/dev/null || true)"
  grep -qE "\"Name\": *\"${A}\"" <<<"$list" || { printf '  %-16s not created\n' "$A"; return; }

  fly machines list -a "$A" --json 2>/dev/null | "$PY" -c '
import json,sys
try: ms = json.load(sys.stdin)
except Exception: ms = []
if not ms: print("  %-16s no machines" % sys.argv[1])
for m in ms:
    g = m.get("config",{}).get("guest",{}) or {}
    print("  %-16s %-9s %s cpu / %s MB  %s" % (
        sys.argv[1], m.get("state","?"),
        g.get("cpus","?"), g.get("memory_mb","?"), m.get("id","")))
' "$A"

  fly volumes list -a "$A" --json 2>/dev/null | "$PY" -c '
import json,sys
try: vs = json.load(sys.stdin)
except Exception: vs = []
for v in vs:
    print("  %-16s volume    %s GB  (%s)" % ("", v.get("size_gb","?"), v.get("name","")))
'
}

echo "Running now"
echo "-----------"
for A in "$APP" "${APP}-postgres" "${APP}-redis"; do show "$A"; done

cat <<'TXT'

Expected shape (anything else is drift, and drift is what costs money)
---------------------------------------------------------------------
  s2p              1 machine   1 cpu / 2048 MB      ~$11.11
  s2p-postgres     1 machine   1 cpu / 1024 MB      ~$5.92
                   1 volume    3 GB                 ~$0.45
  s2p-redis        1 machine   1 cpu /  256 MB      ~$1.94
                                             total  ~$19.50 / month

  Nothing here autoscales: one machine each, fixed sizes, --ha=false. The bill
  is a constant, not a function of traffic. A second machine appearing under
  any app means a deploy created it, not a visitor.

  To stop the meter entirely:  ./deploy/fly/teardown.sh --yes
TXT

echo
echo "Month to date (authoritative): https://fly.io/dashboard (Billing → current month to date)"
