#!/usr/bin/env bash
# Put the playground on the internet, next to the graph it talks to.
#
# Run from the repository root:   ./deploy/fly/deploy-ui.sh
#
# Safe to re-run; this doubles as the redeploy command. `deploy.sh` ships the
# graph — the thing that converts. This ships the page a person opens, which is
# a separate app on purpose: different image (no Node toolchain), different
# memory (512 MB, not 2 GB), and a crash in the UI must never take the API down.
set -euo pipefail

cd "$(dirname "$0")/../.."
HERE="deploy/fly"
REGION="${FLY_REGION:-iad}"
API_APP="${S2P_FLY_APP:-s2p}"
UI_APP="${S2P_FLY_UI_APP:-varun-s2p}"

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

command -v fly >/dev/null || { echo "flyctl is not installed: brew install flyctl"; exit 1; }
fly auth whoami >/dev/null 2>&1 || { echo "Not logged in. Run: fly auth login"; exit 1; }

say "1/3  App ($UI_APP)"
# Same in-process match as deploy.sh: piping into `grep -q` under `set -o
# pipefail` returns 141 when the reader exits early, which would kill the script
# on the "already exists" path.
APPS="$(fly apps list --json 2>/dev/null || true)"
grep -qE "\"Name\": *\"$UI_APP\"" <<<"$APPS" || fly apps create "$UI_APP" --org personal

say "2/3  Secrets"
# The ONLY secret this app gets is the demo key. Deliberately not S2P_API_KEY:
# the owner key bypasses `limits.spend` entirely (guard.guard_run returns True
# for an owner before the meter is reached), so a public page holding it would
# spend the whole day's budget past every guardrail. `playground.suite_key()`
# already refuses to send an owner key to a non-local backend; not putting one
# here means there is nothing to send.
DEMO_KEY="$(sed -n 's/^S2P_DEMO_KEY=//p' .env | tail -1 | tr -d '"'"'"'' || true)"
[ -n "${DEMO_KEY:-}" ] || { echo "No S2P_DEMO_KEY in .env — run ./deploy/fly/deploy.sh first"; exit 1; }
fly secrets set -a "$UI_APP" "S2P_DEMO_KEY=$DEMO_KEY" --stage
echo "   set: 1 secret (value not shown)"

say "3/3  Building and deploying the playground"
# --remote-only for the same reason deploy.sh uses it: Fly's builder is native
# amd64 and this Mac is arm64.
fly deploy -c "$HERE/ui.toml" -a "$UI_APP" \
  --dockerfile "$HERE/Dockerfile.ui" \
  --remote-only --ha=false --yes

say "Done"
URL="https://${UI_APP}.fly.dev"
echo
echo "  Playground: $URL"
echo "  API it calls: https://${API_APP}.fly.dev"
echo
echo "  A custom domain, when you have one:"
echo "    fly certs add <domain> -a $UI_APP     # then add the two DNS records it prints"
