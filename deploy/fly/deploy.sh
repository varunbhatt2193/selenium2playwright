#!/usr/bin/env bash
# Bring the converter up on Fly.io: Postgres, Redis, then the graphs.
#
# Run from the repository root:   ./deploy/fly/deploy.sh
#
# Safe to re-run. Creating an app or a volume that already exists is skipped
# rather than treated as a failure, so this doubles as the redeploy command.
#
# Why three apps instead of one: the API container is the only thing that should
# ever be reachable from the internet. Postgres and Redis get no public IP and
# are addressed over Fly's private network at <name>.internal, which is IPv6 —
# hence the `--bind ::` in redis.toml and PGHOST below. A service listening on
# 127.0.0.1 is invisible to the other machines in the org.
set -euo pipefail

cd "$(dirname "$0")/../.."          # repository root, whatever it is called
HERE="deploy/fly"
REGION="${FLY_REGION:-iad}"
APP="${S2P_FLY_APP:-s2p}"
PG_APP="${APP}-postgres"
REDIS_APP="${APP}-redis"

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

command -v fly >/dev/null || { echo "flyctl is not installed: brew install flyctl"; exit 1; }
fly auth whoami >/dev/null 2>&1 || { echo "Not logged in. Run: fly auth login"; exit 1; }

# The database password is generated once and kept in Fly's secret store, never
# in this repository and never in a shell history. Re-running reuses it: if the
# secret already exists we leave it alone, because rotating it here would
# silently break the POSTGRES_URI the API app already holds.
ensure_app() {
  fly apps list 2>/dev/null | grep -qE "^$1[[:space:]]" || fly apps create "$1" --org personal
}

say "1/6  Postgres app ($PG_APP)"
ensure_app "$PG_APP"
fly volumes list -a "$PG_APP" 2>/dev/null | grep -q s2p_pgdata \
  || fly volumes create s2p_pgdata -a "$PG_APP" -r "$REGION" -n 1 -s 10 --yes

if ! fly secrets list -a "$PG_APP" 2>/dev/null | grep -q POSTGRES_PASSWORD; then
  PGPASS="$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 32)"
  fly secrets set -a "$PG_APP" "POSTGRES_PASSWORD=$PGPASS" --stage
  echo "$PGPASS" > "$HERE/.pgpassword"      # gitignored; needed to build POSTGRES_URI
  chmod 600 "$HERE/.pgpassword"
fi
fly deploy -c "$HERE/postgres.toml" -a "$PG_APP" --ha=false --yes

say "2/6  Redis app ($REDIS_APP)"
ensure_app "$REDIS_APP"
fly deploy -c "$HERE/redis.toml" -a "$REDIS_APP" --ha=false --yes

say "3/6  API app ($APP)"
ensure_app "$APP"

say "4/6  Regenerating the Dockerfile from langgraph.json"
# langgraph.json stays the single source of truth. Regenerating every time means
# a change to the gates' toolchain cannot drift out of the deployed image.
uv run langgraph dockerfile "$HERE/Dockerfile.generated" --config langgraph.json

say "5/6  Secrets"
# Read once, passed straight to Fly, never printed. .env is the developer's copy;
# Fly's secret store is the deployment's. Neither is ever in an image layer.
[ -f "$HERE/.pgpassword" ] || { echo "Missing $HERE/.pgpassword — delete the Postgres secret and re-run"; exit 1; }
PGPASS="$(cat "$HERE/.pgpassword")"
POSTGRES_URI="postgres://postgres:${PGPASS}@${PG_APP}.internal:5432/postgres?sslmode=disable"

# Pull the model + tracing keys out of .env without echoing them. Anything unset
# is simply not forwarded, so a provider you do not use costs nothing.
# Both names, deliberately: the compose file the CLI generates uses POSTGRES_URI,
# while the standalone-server documentation uses DATABASE_URI, and which one the
# pinned image reads is a detail we should not have to be right about. They point
# at the same database, so setting both costs nothing and removes a failure mode
# that would look like "cannot connect" with no clue why.
declare -a SECRETS=(
  "POSTGRES_URI=$POSTGRES_URI"
  "DATABASE_URI=$POSTGRES_URI"
  "REDIS_URI=redis://${REDIS_APP}.internal:6379"
)
for KEY in LANGSMITH_API_KEY ANTHROPIC_API_KEY OPENAI_API_KEY S2P_MODEL S2P_CRITIC_MODEL S2P_EMBEDDINGS; do
  VALUE="$(grep -E "^${KEY}=" .env 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"'"'"'' || true)"
  [ -n "${VALUE:-}" ] && SECRETS+=("$KEY=$VALUE")
done
fly secrets set -a "$APP" "${SECRETS[@]}" --stage
echo "   set: ${#SECRETS[@]} secrets (values not shown)"

say "6/6  Building and deploying the graphs"
# --remote-only: Fly's builder is native amd64. Building this image on an arm64
# Mac would run `npm ci` under emulation.
fly deploy -c "$HERE/app.toml" -a "$APP" \
  --dockerfile "$HERE/Dockerfile.generated" \
  --remote-only --ha=false --yes

say "Done"
fly status -a "$APP" | head -20
URL="https://${APP}.fly.dev"
echo
echo "  URL:    $URL"
echo "  Health: curl $URL/ok"
echo "  Convert: uv run python scripts/call_deployment.py samples/selenium/LoginPage.ts --url $URL"
echo
echo "  Set LANGGRAPH_DEPLOYMENT_URL=$URL in .env to make that --url the default."
