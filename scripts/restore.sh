#!/usr/bin/env bash
# Restore drill — restores a backup into a THROWAWAY database, not the live one.
# Usage:
#   bash scripts/restore.sh <dump-file.sql.gz | dump-file.sql.gz.age>
#
# Accepts plaintext (.sql.gz) or age-encrypted (.sql.gz.age) dumps. Encrypted
# dumps require BACKUP_AGE_IDENTITY (path to your age private key). On the
# droplet you normally drill the LOCAL plaintext dump; you only need the
# identity when rebuilding from a B2 copy on a trusted recovery machine.
#
# This is intentionally safe: it restores into 'memory_restore_test', not 'memory'.
# To actually replace the live DB, you would stop the app stack, drop the live DB,
# and restore into it — but only after you've verified you really meant it.

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <dump-file.sql.gz>"
  exit 1
fi

DUMP_FILE="$1"
if [[ ! -f "$DUMP_FILE" ]]; then
  echo "File not found: $DUMP_FILE"
  exit 1
fi

# shellcheck disable=SC1091
source /srv/memory/secrets/.env

: "${POSTGRES_USER:?}"

TEST_DB="memory_restore_test"

echo "Restoring $DUMP_FILE into '$TEST_DB' (throwaway database)..."

docker compose -f /srv/memory/docker-compose.yml --env-file /srv/memory/secrets/.env \
  exec -T postgres dropdb -U "$POSTGRES_USER" --if-exists "$TEST_DB"

docker compose -f /srv/memory/docker-compose.yml --env-file /srv/memory/secrets/.env \
  exec -T postgres createdb -U "$POSTGRES_USER" "$TEST_DB"

# Decrypt-if-needed, then decompress, then load. Streams — no plaintext temp file.
decrypt_stream() {
  if [[ "$DUMP_FILE" == *.age ]]; then
    : "${BACKUP_AGE_IDENTITY:?restoring a .age dump needs BACKUP_AGE_IDENTITY (path to your age private key — kept OFF the droplet)}"
    age -d -i "$BACKUP_AGE_IDENTITY" "$DUMP_FILE"
  else
    cat "$DUMP_FILE"
  fi
}

# The throwaway DB lives in the LIVE Postgres container, so the restore shares
# its /dev/shm with production queries. Parallel index builds (CREATE INDEX with
# maintenance workers) allocate dynamic shared memory there; on 2026-11-02 they
# filled it and a concurrent normalizer query failed with "No space left on
# device". Build indexes serially in this session only — slower, but it can't
# starve the live workload.
decrypt_stream | gunzip | docker compose -f /srv/memory/docker-compose.yml \
  --env-file /srv/memory/secrets/.env \
  exec -T -e PGOPTIONS="-c max_parallel_maintenance_workers=0" \
  postgres psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" "$TEST_DB"

echo
echo "Restore drill complete. Inspect $TEST_DB before dropping:"
echo "  docker compose -f /srv/memory/docker-compose.yml exec postgres \\"
echo "    psql -U $POSTGRES_USER $TEST_DB -c '\\dt'"
echo
echo "When done, drop with:"
echo "  docker compose -f /srv/memory/docker-compose.yml exec postgres \\"
echo "    dropdb -U $POSTGRES_USER $TEST_DB"
