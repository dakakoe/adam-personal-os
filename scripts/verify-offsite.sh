#!/usr/bin/env bash
# Off-site backup VERIFICATION drill — proves the encrypted off-site DB backup
# is actually recoverable end-to-end: it downloads the newest
# memory-*.sql.gz.age from B2, DECRYPTS it with your age private key, and checks
# the result. This is the piece the on-droplet restore-drill can't do.
#
#   ┌──────────────────────────────────────────────────────────────────────┐
#   │  RUN THIS ON THE MACHINE THAT HOLDS THE AGE PRIVATE KEY (your Mac),     │
#   │  NOT the droplet. The droplet deliberately holds only the public        │
#   │  recipient and cannot decrypt its own off-site backups — so a wrong or  │
#   │  lost age key, or a corrupt B2 object, is INVISIBLE until you actually   │
#   │  need to recover. This drill is the only thing that catches it early.   │
#   │  Run it periodically (e.g. monthly) and after any key/recipient change.  │
#   └──────────────────────────────────────────────────────────────────────┘
#
# Usage:
#   BACKUP_ENV=~/secrets/backup.env bash scripts/verify-offsite.sh         # quick check
#   BACKUP_ENV=~/secrets/backup.env bash scripts/verify-offsite.sh --full  # + real restore
#
# --full additionally streams the decrypted dump over `ssh memory` into a
# throwaway DB on the droplet (memory_restore_test) for a complete restore — the
# private key stays in memory on THIS machine and never lands on the droplet.
#
# Requires (on this machine): age, aws CLI, gzip; and these vars (from BACKUP_ENV
# file if set, else the environment):
#   BACKUP_AGE_IDENTITY   path to your age private key (identity)
#   BACKUP_S3_ENDPOINT BACKUP_S3_BUCKET BACKUP_S3_KEY BACKUP_S3_SECRET

set -euo pipefail

if [[ -n "${BACKUP_ENV:-}" ]]; then
  # shellcheck disable=SC1090
  source "$BACKUP_ENV"
fi

: "${BACKUP_AGE_IDENTITY:?path to your age private key — this drill is pointless without it}"
: "${BACKUP_S3_ENDPOINT:?}"
: "${BACKUP_S3_BUCKET:?}"
: "${BACKUP_S3_KEY:?}"
: "${BACKUP_S3_SECRET:?}"
[[ -f "$BACKUP_AGE_IDENTITY" ]] || { echo "age identity not found: $BACKUP_AGE_IDENTITY"; exit 1; }

FULL=0
[[ "${1:-}" == "--full" ]] && FULL=1

export AWS_ACCESS_KEY_ID="$BACKUP_S3_KEY" AWS_SECRET_ACCESS_KEY="$BACKUP_S3_SECRET"
aws_s3() { aws --endpoint-url "$BACKUP_S3_ENDPOINT" "$@"; }

# Newest encrypted DB backup. Keys are memory-<ISO8601>.sql.gz.age (write-once),
# so lexical sort == chronological.
LATEST=$(aws_s3 s3api list-object-versions --bucket "$BACKUP_S3_BUCKET" \
           --query 'Versions[].Key' --output text 2>/dev/null | tr '\t' '\n' \
         | grep -E '^memory-.*\.sql\.gz\.age$' | sort -u | tail -1)
[[ -n "$LATEST" ]] || { echo "FAIL: no memory-*.sql.gz.age found in B2 — is the off-site upload working?"; exit 1; }
echo "[$(date -u +%FT%TZ)] verifying off-site backup: $LATEST"

TMP="$(mktemp -t verify-offsite.XXXXXX.age)"
trap 'rm -f "$TMP"' EXIT
aws_s3 s3 cp "s3://$BACKUP_S3_BUCKET/$LATEST" "$TMP" --no-progress >/dev/null
echo "[$(date -u +%FT%TZ)] downloaded $(du -h "$TMP" | cut -f1) — decrypting + checking"

# Decrypt with the private key, decompress, and confirm it's a real pg_dump.
# Done as a non-pipefail block because the grep short-circuits and SIGPIPEs the
# upstream age/gunzip, which pipefail would otherwise report as a failure.
set +o pipefail
age -d -i "$BACKUP_AGE_IDENTITY" "$TMP" | gunzip 2>/dev/null \
  | grep -qam1 -E 'PostgreSQL database dump|^CREATE TABLE|COPY '
CHECK=$?
set -o pipefail
if [[ "$CHECK" -ne 0 ]]; then
  echo "[$(date -u +%FT%TZ)] FAIL: decrypt/decompress produced no recognizable dump content."
  echo "  → either the age key doesn't match this object, or the B2 object is corrupt."
  exit 1
fi
echo "[$(date -u +%FT%TZ)] OK: decrypted with your key, gzip intact, content is a Postgres dump."

if [[ "$FULL" -eq 1 ]]; then
  echo "[$(date -u +%FT%TZ)] --full: restoring into throwaway DB 'memory_restore_test' on the droplet"
  DC='docker compose -f /srv/memory/docker-compose.yml --env-file /srv/memory/secrets/.env'
  ssh memory "$DC exec -T postgres dropdb -U \$(grep -E '^POSTGRES_USER=' /srv/memory/secrets/.env | cut -d= -f2-) --if-exists memory_restore_test; \
              $DC exec -T postgres createdb -U \$(grep -E '^POSTGRES_USER=' /srv/memory/secrets/.env | cut -d= -f2-) memory_restore_test"
  # Decrypt locally (key stays here), stream plaintext over SSH into the droplet's psql.
  age -d -i "$BACKUP_AGE_IDENTITY" "$TMP" | gunzip \
    | ssh memory "$DC exec -T postgres psql -v ON_ERROR_STOP=1 -U \$(grep -E '^POSTGRES_USER=' /srv/memory/secrets/.env | cut -d= -f2-) memory_restore_test >/dev/null"
  CNT=$(ssh memory "$DC exec -T postgres psql -U \$(grep -E '^POSTGRES_USER=' /srv/memory/secrets/.env | cut -d= -f2-) memory_restore_test -tAc 'SELECT count(*) FROM raw.telegram_message'" | tr -d '[:space:]')
  ssh memory "$DC exec -T postgres dropdb -U \$(grep -E '^POSTGRES_USER=' /srv/memory/secrets/.env | cut -d= -f2-) --if-exists memory_restore_test" >/dev/null
  if [[ -z "$CNT" || "$CNT" == "0" ]]; then
    echo "[$(date -u +%FT%TZ)] FAIL: full restore loaded but raw.telegram_message=${CNT:-<none>} (expected > 0)"
    exit 1
  fi
  echo "[$(date -u +%FT%TZ)] OK (--full): real restore from the encrypted off-site copy, raw.telegram_message=$CNT rows."
fi

echo "[$(date -u +%FT%TZ)] off-site verification PASSED."
