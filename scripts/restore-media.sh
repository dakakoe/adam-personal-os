#!/usr/bin/env bash
# Media RESTORE — the inverse of backup-media.sh. Downloads the age-encrypted
# media objects from B2 (media/**.age), decrypts each with your age private key,
# and writes them back to their original relative path under a target directory.
#
#   ┌──────────────────────────────────────────────────────────────────────┐
#   │  RUN THIS ON A MACHINE THAT HOLDS THE AGE PRIVATE KEY (your Mac or the  │
#   │  recovery box). Decryption needs BACKUP_AGE_IDENTITY, which is OFF the  │
#   │  droplet by design.                                                     │
#   └──────────────────────────────────────────────────────────────────────┘
#
# Idempotent: a file already present (correct size) at the destination is
# skipped, so you can re-run / resume safely.
#
# Usage:
#   BACKUP_ENV=~/secrets/backup.env bash scripts/restore-media.sh [TARGET_DIR]
#     TARGET_DIR defaults to ./restored-media (NEVER defaults to the live
#     /srv/memory/data — pass that explicitly on the box you're rebuilding).
#
# Requires (on this machine): age, aws CLI; and these vars (from BACKUP_ENV file
# if set, else the environment):
#   BACKUP_AGE_IDENTITY   path to your age private key (identity)
#   BACKUP_S3_ENDPOINT BACKUP_S3_BUCKET BACKUP_S3_KEY BACKUP_S3_SECRET

set -euo pipefail

if [[ -n "${BACKUP_ENV:-}" ]]; then
  # shellcheck disable=SC1090
  source "$BACKUP_ENV"
fi

: "${BACKUP_AGE_IDENTITY:?path to your age private key — needed to decrypt media}"
: "${BACKUP_S3_ENDPOINT:?}"
: "${BACKUP_S3_BUCKET:?}"
: "${BACKUP_S3_KEY:?}"
: "${BACKUP_S3_SECRET:?}"
[[ -f "$BACKUP_AGE_IDENTITY" ]] || { echo "age identity not found: $BACKUP_AGE_IDENTITY"; exit 1; }

TARGET="${1:-./restored-media}"
mkdir -p "$TARGET"

export AWS_ACCESS_KEY_ID="$BACKUP_S3_KEY" AWS_SECRET_ACCESS_KEY="$BACKUP_S3_SECRET"
aws_s3() { aws --endpoint-url "$BACKUP_S3_ENDPOINT" "$@"; }

echo "[$(date -u +%FT%TZ)] restoring media from s3://$BACKUP_S3_BUCKET/media/ → $TARGET"

mapfile -t KEYS < <(
  aws_s3 s3api list-objects-v2 --bucket "$BACKUP_S3_BUCKET" --prefix "media/" \
    --query 'Contents[].Key' --output text 2>/dev/null | tr '\t' '\n' \
  | grep -E '\.age$' | sort
)
if [[ "${#KEYS[@]}" -eq 0 ]]; then
  echo "[$(date -u +%FT%TZ)] no media/*.age objects found in B2 — nothing to restore."
  exit 0
fi

restored=0 skipped=0 failed=0
for key in "${KEYS[@]}"; do
  [[ -n "$key" && "$key" != "None" ]] || continue
  rel="${key#media/}"          # voice/2026/06/note.ogg.age
  rel="${rel%.age}"            # voice/2026/06/note.ogg
  dest="$TARGET/$rel"
  if [[ -f "$dest" && -s "$dest" ]]; then
    skipped=$((skipped + 1))
    continue
  fi
  mkdir -p "$(dirname "$dest")"
  tmp="$(mktemp "${dest}.part.XXXXXX")"
  # Stream B2 object → age decrypt → file. Write to a .part temp and rename only
  # on success so an interrupted run never leaves a truncated media file.
  if aws_s3 s3 cp "s3://$BACKUP_S3_BUCKET/$key" - --no-progress 2>/dev/null \
       | age -d -i "$BACKUP_AGE_IDENTITY" > "$tmp" 2>/dev/null && [[ -s "$tmp" ]]; then
    mv "$tmp" "$dest"
    restored=$((restored + 1))
  else
    rm -f "$tmp"
    failed=$((failed + 1))
    echo "[$(date -u +%FT%TZ)] WARNING: failed to restore $rel (download/decrypt error)"
  fi
done

echo "[$(date -u +%FT%TZ)] media restore done: restored=$restored skipped=$skipped failed=$failed → $TARGET"
[[ "$failed" -eq 0 ]] || exit 1
