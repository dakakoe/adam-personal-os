#!/usr/bin/env bash
# Parse every SQL statement in queries.py against a real Postgres, using the
# WORKING TREE — before it ships. See merge_api/tests/test_sql_smoke.py.
#
#   ./scripts/sql-smoke.sh
#
# The schema comes from production, dumped structure-only into a throwaway
# database. That's deliberate: the question this answers is "will these
# statements parse against the schema they will actually meet", and a
# from-migrations rebuild answers a subtly different one. No data is copied and
# nothing runs against the live database beyond a read-only pg_dump.
#
# The droplet is the gate (it has Postgres and the venv), so the local tree is
# shipped to a temp dir there and the test runs against the container.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST="${SMOKE_HOST:-memory}"
PG="${SMOKE_PG_CONTAINER:-memory-postgres-1}"
SRC_DB="${SMOKE_SOURCE_DB:-memory}"
DB="${SMOKE_DB:-sql_smoke}"
REMOTE_DIR="/tmp/sql-smoke"
VENV="${SMOKE_VENV:-/srv/memory/apps/merge_api/.venv/bin/python}"

echo "==> shipping working tree to $HOST:$REMOTE_DIR"
ssh "$HOST" "rm -rf $REMOTE_DIR && mkdir -p $REMOTE_DIR"
rsync -az --exclude '__pycache__' --exclude '.venv' \
      "$REPO_ROOT/merge_api/" "$HOST:$REMOTE_DIR/merge_api/"

echo "==> building scratch schema '$DB' from $SRC_DB (structure only)"
ssh "$HOST" PG="$PG" SRC_DB="$SRC_DB" DB="$DB" bash -s <<'REMOTE'
set -euo pipefail
U=$(docker exec "$PG" printenv POSTGRES_USER)
docker exec "$PG" psql -U "$U" -d postgres -q \
  -c "DROP DATABASE IF EXISTS $DB;" -c "CREATE DATABASE $DB;"
docker exec "$PG" pg_dump -U "$U" --schema-only --no-owner --no-privileges "$SRC_DB" \
  | docker exec -i "$PG" psql -U "$U" -d "$DB" -q -v ON_ERROR_STOP=0 >/dev/null 2>&1
n=$(docker exec "$PG" psql -U "$U" -d "$DB" -tAc "SELECT count(*) FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog','information_schema');")
echo "    tables: $n"
REMOTE

echo "==> preparing every statement"
set +e
ssh "$HOST" PG="$PG" DB="$DB" REMOTE_DIR="$REMOTE_DIR" VENV="$VENV" bash -s <<'REMOTE'
U=$(docker exec "$PG" printenv POSTGRES_USER)
P=$(docker exec "$PG" printenv POSTGRES_PASSWORD)
cd "$REMOTE_DIR/merge_api"
SMOKE_DATABASE_URL="postgresql://$U:$P@127.0.0.1:5432/$DB" \
  "$VENV" -m pytest tests/test_sql_smoke.py -q 2>&1 | tail -40
REMOTE
STATUS=$?
set -e

echo "==> dropping scratch schema"
ssh "$HOST" PG="$PG" DB="$DB" REMOTE_DIR="$REMOTE_DIR" bash -s <<'REMOTE' || true
U=$(docker exec "$PG" printenv POSTGRES_USER)
docker exec "$PG" psql -U "$U" -d postgres -q -c "DROP DATABASE IF EXISTS $DB;"
rm -rf "$REMOTE_DIR"
REMOTE

exit $STATUS
