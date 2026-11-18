#!/usr/bin/env bash
# Start a disposable, headless Braidplane Core (API + worker + PostgreSQL) for trying this MCP server.
#
#   scripts/local-core.sh up <path-to-braidplane-core-checkout>
#   scripts/local-core.sh down <path-to-braidplane-core-checkout>
#
# Needs only Docker and curl: the Core binaries are compiled inside the Go image that Core's own
# Dockerfile pins, so no local Go toolchain is required. (Core's documented full-stack path is
# `task dev`; see its COMMUNITY_BUILD.md. This script skips the web frontend and mail server.)
#
# Everything is synthetic and local: Core's own fixture seeder (`cmd/fake --core-only`) creates a
# test organization, project and API key; scripts/seed_local.sql adds three pipelines with runs.
# The generated API key is written to ./.env (mode 600) and never printed.
#
# Ports (override by exporting before the call): DB_PORT=35432 SERVER_PORT=3011 WORKER_PORT=3013
# Compose project: COMPOSE_PROJECT_NAME=braidplane-mcp-local
set -euo pipefail

cmd="${1:?usage: local-core.sh up|down <core-checkout>}"
core="$(cd "${2:?path to a braidplane-core checkout}" && pwd)"
here="$(cd "$(dirname "$0")/.." && pwd)"
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-braidplane-mcp-local}"
DB_PORT="${DB_PORT:-35432}"; SERVER_PORT="${SERVER_PORT:-3011}"; WORKER_PORT="${WORKER_PORT:-3013}"
state="$core/.worktree/mcp-local"
mkdir -p "$state"

if [ "$cmd" = down ]; then
    for f in api worker; do [ -f "$state/$f.pid" ] && kill "$(cat "$state/$f.pid")" 2>/dev/null || true; rm -f "$state/$f.pid"; done
    (cd "$core" && DB_PORT="$DB_PORT" docker compose down --volumes database)
    exit 0
fi
[ "$cmd" = up ] || { echo "unknown command: $cmd" >&2; exit 2; }

cd "$core"
image="$(grep -m1 '^FROM golang' Dockerfile | awk '{print $2}')"
echo ">> building Core binaries with $image (first run downloads Go modules)"
mkdir -p out "$state/gocache" "$state/gomod"
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -e GOCACHE=/cache/build -e GOMODCACHE=/cache/mod \
    -e GOWORK=off -e CGO_ENABLED=0 -v "$PWD:/src" -v "$state/gocache:/cache/build" -v "$state/gomod:/cache/mod" -w /src "$image" \
    bash -c 'go build -trimpath -o out/braidplane-server . && go build -trimpath -o out/migrate ./cmd/migrate && go build -trimpath -o out/fake ./cmd/fake'

echo ">> starting PostgreSQL on 127.0.0.1:$DB_PORT (compose project $COMPOSE_PROJECT_NAME)"
DB_PORT="$DB_PORT" docker compose up -d --wait database

if [ ! -f "$state/keys.env" ]; then
    ( umask 077
      printf 'export ENCRYPTION_KEY_HEX=%s\nexport EXECUTION_REQUEST_KEY_HEX=%s\n' "$(openssl rand -hex 32)" "$(openssl rand -hex 32)" > "$state/keys.env" )
fi
# shellcheck disable=SC1091
source "$state/keys.env"
export DB_HOST=127.0.0.1 DB_PORT DB_USER=postgres DB_PASSWORD=postgres DB_DATABASE=braidplane DB_SSL_MODE=disable
export DEPLOYMENT_PROFILE=local CONFIG_PATH="$core/config.toml" SELF_SERVICE_REGISTRATION_ENABLED=false
export SERVER_HOST=127.0.0.1 SERVER_PORT FRONTEND_SERVER_HOST=127.0.0.1 FRONTEND_SERVER_PORT=3012
export FRONTEND_URL=http://127.0.0.1:3012 BACKEND_API_URL="http://127.0.0.1:$SERVER_PORT"

echo ">> applying migrations"
./out/migrate up --path postgres/migrations --ledger core_schema_migrations
nohup ./out/braidplane-server api > "$state/api.log" 2>&1 & echo $! > "$state/api.pid"
SERVER_PORT="$WORKER_PORT" nohup ./out/braidplane-server worker > "$state/worker.log" 2>&1 & echo $! > "$state/worker.pid"
for _ in $(seq 1 60); do curl -fsS "http://127.0.0.1:$SERVER_PORT/readyz" >/dev/null 2>&1 && break; sleep 1; done
curl -fsS "http://127.0.0.1:$SERVER_PORT/readyz" >/dev/null || { echo "API did not become ready; see $state/api.log" >&2; exit 1; }

echo ">> seeding synthetic fixtures"
( umask 077; ./out/fake --config config.toml --core-only > "$state/seed.out" 2>&1 )
org="$(grep '^organization_slug=' "$state/seed.out" | cut -d= -f2)"
project="$(grep '^project_slug=' "$state/seed.out" | cut -d= -f2)"
token="$(grep '^api_key_token=' "$state/seed.out" | cut -d= -f2-)"
docker exec -i "${COMPOSE_PROJECT_NAME}-database" psql -q -U postgres braidplane -v org="$org" -v project="$project" \
    < "$here/scripts/seed_local.sql" >/dev/null

( umask 077; cat > "$here/.env" <<ENV
API_BASE_URL=http://127.0.0.1:$SERVER_PORT/api
API_TOKEN=$token
CORE_ORG=$org
CORE_PROJECT=$project
CORE_ENVIRONMENT=dev
ENV
)
echo ">> ready: Core API at http://127.0.0.1:$SERVER_PORT/api, MCP settings written to $here/.env (not printed)"
echo "   try:  cd $here && EXPECT_SEED=1 uv run python scripts/live_check.py"
