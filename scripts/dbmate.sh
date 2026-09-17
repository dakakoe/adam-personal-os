#!/usr/bin/env bash
# Run dbmate against the droplet's Postgres via the docker internal network.
# Usage (on the droplet): scripts/dbmate.sh <up|status|new NAME|down|...>
#
# Why a wrapper:
#   - Postgres isn't exposed to localhost; we have to enter `memory_internal`.
#   - The DB user is `memory`, which means search_path is "$user",public — and
#     once our first migration creates a `memory` schema, an unqualified
#     `schema_migrations` reference resolves there instead of public. The
#     --migrations-table flag pins it to public so dbmate stays consistent.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MIGRATIONS_DIR="${MIGRATIONS_DIR:-${REPO_ROOT}/migrations}"
ENV_FILE="${ENV_FILE:-/srv/memory/secrets/.env}"
DOCKER_NETWORK="${DOCKER_NETWORK:-memory_internal}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "error: $ENV_FILE not found. Run this on the droplet." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

exec docker run --rm \
  --network "$DOCKER_NETWORK" \
  -v "$MIGRATIONS_DIR":/db/migrations \
  -e DATABASE_URL="postgres://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}?sslmode=disable" \
  ghcr.io/amacneil/dbmate \
  --migrations-table public.schema_migrations \
  "$@"
