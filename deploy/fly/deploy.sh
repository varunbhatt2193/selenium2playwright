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
# Every existence check below captures the output and matches it in-process
# rather than piping into `grep -q`. Under `set -o pipefail` a pipeline whose
# reader exits early returns 141 (SIGPIPE), which `set -e` treats as failure —
# so the "already exists, skip it" path would kill the script instead.
ensure_app() {
  local list
  list="$(fly apps list --json 2>/dev/null || true)"
  grep -qE "\"Name\": *\"$1\"" <<<"$list" || fly apps create "$1" --org personal
}

say "1/6  Postgres app ($PG_APP)"
ensure_app "$PG_APP"
VOLUMES="$(fly volumes list -a "$PG_APP" --json 2>/dev/null || true)"
grep -q '"name": *"s2p_pgdata"' <<<"$VOLUMES" \
  || fly volumes create s2p_pgdata -a "$PG_APP" -r "$REGION" -n 1 -s 3 --yes

PG_SECRETS="$(fly secrets list -a "$PG_APP" --json 2>/dev/null || true)"
if ! grep -q POSTGRES_PASSWORD <<<"$PG_SECRETS"; then
  # openssl rather than `tr </dev/urandom | head -c 32`: that pipeline SIGPIPEs.
  # Hex only, so the password can never need escaping inside POSTGRES_URI.
  PGPASS="$(openssl rand -hex 24)"
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
# The demo is only safe because it has keys, so the deploy generates them rather
# than trusting anyone to remember. Written into .env, which is both the local
# developer's copy and — because the loop below forwards everything in it — the
# source the deployment's secrets are read from. Generated once and never
# rotated here: changing S2P_DEMO_KEY would silently lock out a playground that
# is already configured with the old one.
for KEYNAME in S2P_API_KEY S2P_DEMO_KEY; do
  if ! grep -qE "^${KEYNAME}=" .env 2>/dev/null; then
    printf '%s=%s\n' "$KEYNAME" "$(openssl rand -hex 32)" >> .env
    echo "   generated $KEYNAME into .env (value not shown)"
  fi
done

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
# Forward every key in .env rather than a hand-maintained list. A list is
# exactly the wrong shape here: it fails silently and late. ANTHROPIC_WORKSPACE_ID
# was missing from one, so the container authenticated fine and then took a 400
# from Anthropic on every call, because an identity-linked key must name the
# workspace it acts in (llm.py sends it as the anthropic-workspace-id header).
# The skip list below is short and is about things the deployment defines for
# itself, not about which providers we happen to use.
SKIP="POSTGRES_URI DATABASE_URI REDIS_URI PORT S2P_SANDBOX LANGGRAPH_DEPLOYMENT_URL"
while IFS= read -r KEY; do
  case " $SKIP " in *" $KEY "*) continue ;; esac
  VALUE="$(sed -n "s/^${KEY}=//p" .env | tail -1 | tr -d '"'"'"'' || true)"
  [ -n "${VALUE:-}" ] && SECRETS+=("$KEY=$VALUE")
done < <(sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p' .env | sort -u)
fly secrets set -a "$APP" "${SECRETS[@]}" --stage
echo "   set: ${#SECRETS[@]} secrets (values not shown)"

say "6/6  Building and deploying the graphs"
# --remote-only: Fly's builder is native amd64. Building this image on an arm64
# Mac would run `npm ci` under emulation.
fly deploy -c "$HERE/app.toml" -a "$APP" \
  --dockerfile "$HERE/Dockerfile.generated" \
  --remote-only --ha=false --yes

say "Done"
fly status -a "$APP" 2>/dev/null | sed -n "1,20p"
URL="https://${APP}.fly.dev"
echo
echo "  URL:    $URL"
echo "  Health: curl $URL/ok"
echo "  Convert: uv run python scripts/call_deployment.py samples/selenium/LoginPage.ts --url $URL"
echo
echo "  Set LANGGRAPH_DEPLOYMENT_URL=$URL in .env to make that --url the default."
