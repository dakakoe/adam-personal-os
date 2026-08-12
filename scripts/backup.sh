#!/usr/bin/env bash
# Daily Postgres dump → off-droplet object storage.
# Run via memory-backup.timer (03:00 UTC).

set -euo pipefail

# shellcheck disable=SC1091
source /srv/memory/secrets/.env

: "${POSTGRES_USER:?}"
: "${POSTGRES_DB:?}"

TIMESTAMP=$(date -u +%Y%m%dT%H%M%SZ)
DUMP_FILE="/srv/memory/backups/memory-${TIMESTAMP}.sql.gz"

mkdir -p /srv/memory/backups

# Fail CLOSED: if an off-site destination is configured, encryption is
# mandatory — the dump carries Google refresh tokens, the Telegram session's
# equivalents, source_secret API keys, and every contact/message/finance row,
# and a third-party bucket must never see any of it in cleartext. (Matches
# backup-media.sh, which already hard-requires the recipient.) A missing
# recipient here previously only WARNED and uploaded plaintext — the exact
# friend-install footgun this closes. Local-only backups (no S3) stay
# plaintext by design: the droplet already holds the live DB in cleartext.
if [[ -n "${BACKUP_S3_ENDPOINT:-}" && -n "${BACKUP_S3_BUCKET:-}" \
      && -n "${BACKUP_S3_KEY:-}" && -n "${BACKUP_S3_SECRET:-}" \
      && -z "${BACKUP_AGE_RECIPIENT:-}" ]]; then
  echo "[$(date -u +%FT%TZ)] FATAL: off-site backup is configured but BACKUP_AGE_RECIPIENT is unset." >&2
  echo "  Refusing to upload an UNENCRYPTED database dump to a third-party bucket." >&2
  echo "  Set BACKUP_AGE_RECIPIENT (an age public key) in /srv/memory/secrets/.env, or unset the BACKUP_S3_* vars for local-only backups." >&2
  exit 1
fi

# Exclude the embeddings table DATA — ~3.5GB of 768-dim vectors the local e5
# embedder fully regenerates from canonical.interaction. We keep its schema
# (restore recreates the empty table; the embedder repopulates it), just drop
# the rows from the backup. Halves the dump and keeps us in B2's free tier.
# canonical.interaction (the source) IS backed up, so recovery is complete —
# embeddings just rebuild in the background after a restore.
echo "[$(date -u +%FT%TZ)] pg_dump starting (excluding memory.interaction_embedding data)"
docker compose -f /srv/memory/docker-compose.yml --env-file /srv/memory/secrets/.env \
  exec -T postgres pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" \
    --exclude-table-data='memory.interaction_embedding' | gzip > "$DUMP_FILE"

DUMP_SIZE=$(du -h "$DUMP_FILE" | cut -f1)
echo "[$(date -u +%FT%TZ)] dump written: $DUMP_FILE ($DUMP_SIZE)"

# Encrypt the artifact that leaves the droplet. age public-key (recipient)
# encryption: the recipient string lives on the droplet; only the matching
# private identity — kept OFF the droplet (your Mac + password manager) — can
# decrypt. The local dump stays plaintext for fast on-droplet restore drills
# (the droplet already holds the live DB in plaintext, so this adds no risk),
# while B2 (a third party) only ever sees ciphertext.
UPLOAD_FILE="$DUMP_FILE"
REMOTE_NAME="memory-${TIMESTAMP}.sql.gz"
if [[ -n "${BACKUP_AGE_RECIPIENT:-}" ]]; then
  echo "[$(date -u +%FT%TZ)] encrypting off-site copy with age"
  age -r "$BACKUP_AGE_RECIPIENT" -o "${DUMP_FILE}.age" "$DUMP_FILE"
  UPLOAD_FILE="${DUMP_FILE}.age"
  REMOTE_NAME="memory-${TIMESTAMP}.sql.gz.age"
fi
# No `else` branch: the guard above guarantees that whenever an off-site
# upload can happen, BACKUP_AGE_RECIPIENT is set. Without S3 configured the
# dump stays local-only (plaintext, by design).

# Off-site upload (S3-compatible). Skip silently if S3 vars are not configured yet.
UPLOAD_RC=0
if [[ -n "${BACKUP_S3_ENDPOINT:-}" && -n "${BACKUP_S3_BUCKET:-}" \
      && -n "${BACKUP_S3_KEY:-}" && -n "${BACKUP_S3_SECRET:-}" ]]; then
  echo "[$(date -u +%FT%TZ)] uploading to $BACKUP_S3_BUCKET"
  # Non-fatal: a failed off-site upload (e.g. storage cap) must NOT abort the
  # run — the local dump is still valuable and local pruning must still run.
  # We record the failure and exit non-zero at the end so it's visible.
  if AWS_ACCESS_KEY_ID="$BACKUP_S3_KEY" AWS_SECRET_ACCESS_KEY="$BACKUP_S3_SECRET" \
     aws --endpoint-url "$BACKUP_S3_ENDPOINT" s3 cp "$UPLOAD_FILE" \
       "s3://$BACKUP_S3_BUCKET/$REMOTE_NAME" --no-progress; then
    echo "[$(date -u +%FT%TZ)] upload complete"

    # Prune REMOTE copies — keep the newest $REMOTE_KEEP. Keys are
    # memory-<ISO8601>.sql.gz (write-once, unique), so lexical sort ==
    # chronological. IMPORTANT: B2 keeps file versions — a plain `s3 rm` only
    # adds a delete-marker and the bytes stay (this is what blew past the free
    # tier). So we HARD-delete every version id of each pruned key.
    REMOTE_KEEP="${BACKUP_REMOTE_KEEP:-7}"
    export AWS_ACCESS_KEY_ID="$BACKUP_S3_KEY" AWS_SECRET_ACCESS_KEY="$BACKUP_S3_SECRET"
    mapfile -t REMOTE_KEYS < <(
      aws --endpoint-url "$BACKUP_S3_ENDPOINT" s3api list-object-versions --bucket "$BACKUP_S3_BUCKET" \
        --query 'Versions[].Key' --output text 2>/dev/null | tr '\t' '\n' \
        | grep -E '^memory-.*\.sql\.gz(\.age)?$' | sort -u
    )
    PRUNE_COUNT=$(( ${#REMOTE_KEYS[@]} - REMOTE_KEEP ))
    if (( PRUNE_COUNT > 0 )); then
      for k in "${REMOTE_KEYS[@]:0:PRUNE_COUNT}"; do
        echo "[$(date -u +%FT%TZ)] pruning remote (all versions) $k"
        aws --endpoint-url "$BACKUP_S3_ENDPOINT" s3api list-object-versions \
          --bucket "$BACKUP_S3_BUCKET" --prefix "$k" \
          --query 'Versions[].VersionId' --output text 2>/dev/null | tr '\t' '\n' \
          | while read -r vid; do
              [[ -n "$vid" && "$vid" != "None" ]] && \
              aws --endpoint-url "$BACKUP_S3_ENDPOINT" s3api delete-object \
                --bucket "$BACKUP_S3_BUCKET" --key "$k" --version-id "$vid" >/dev/null
            done
      done
    fi
    unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY
  else
    UPLOAD_RC=1
    echo "[$(date -u +%FT%TZ)] WARNING: off-site upload failed (storage cap?) — local dump kept"
  fi
else
  echo "[$(date -u +%FT%TZ)] S3 vars not set; skipping off-site upload"
fi

# Drop the transient encrypted upload artifact; the local copy stays plaintext
# (used by the on-droplet restore drill). No-op if encryption was skipped.
rm -f "${DUMP_FILE}.age"

# Prune local copies — keep last 7 (runs even if the off-site upload failed).
# Globs plaintext .sql.gz only; never matches the transient .sql.gz.age above.
ls -1t /srv/memory/backups/memory-*.sql.gz | tail -n +8 | xargs -r rm -v

echo "[$(date -u +%FT%TZ)] done (upload_rc=$UPLOAD_RC)"
exit "$UPLOAD_RC"
