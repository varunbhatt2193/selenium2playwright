#!/usr/bin/env bash
# Destroy everything this project runs on Fly, and stop the meter.
#
#   ./deploy/fly/teardown.sh            # asks before each app
#   ./deploy/fly/teardown.sh --yes      # no questions
#
# Fly bills for machines and volumes by the second while they exist, whether or
# not anyone is using them. Stopping a machine still bills its volume. Only
# destroying the app stops the charge completely, which is why this exists as a
# one-liner rather than a paragraph in a README: the cheapest deployment is the
# one you can turn off without thinking about it.
#
# This deletes the Postgres volume, and with it every thread and memory the
# deployed agent ever wrote. Local development is untouched.
set -euo pipefail

cd "$(dirname "$0")/../.."
APP="${S2P_FLY_APP:-s2p}"
YES=""
[ "${1:-}" = "--yes" ] && YES="--yes"

for A in "$APP" "${APP}-redis" "${APP}-postgres"; do
  APPS="$(fly apps list --json 2>/dev/null || true)"
  if grep -qE "\"Name\": *\"${A}\"" <<<"$APPS"; then
    echo "destroying $A"
    fly apps destroy "$A" $YES
  else
    echo "skip $A (does not exist)"
  fi
done

# The password only means anything while the database exists.
rm -f deploy/fly/.pgpassword
echo
echo "Nothing left running. Re-create it any time with ./deploy/fly/deploy.sh"
