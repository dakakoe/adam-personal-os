#!/usr/bin/env bash
# Incremental, encrypted media backup → off-site object storage.
# Run via memory-backup-media.timer (weekly, 04:00 UTC Sun).
#
# Media (voice notes, receipt photos, …) is NOT in the Postgres dump — it lives
# on disk under /srv/memory/data/. Without this, losing the droplet loses every
# voice note and photo, even with perfect DB backups.
#
# Design: media is append-only and immutable (a voice note never changes once
# written), so we back it up INCREMENTALLY — each file is age-encrypted and
# uploaded exactly once, then skipped on every later run. age public-key
# encryption means B2 only ever holds ciphertext; only the private identity
# (kept OFF the droplet) can decrypt. We never prune media off-site — it is
# irreplaceable.

set -euo pipefail

# shellcheck disable=SC1091
source /srv/memory/secrets/.env

# All four S3 vars + an age recipient are REQUIRED — media must never leave
# the box unencrypted, and there's no point running without a destination.
: "${BACKUP_S3_ENDPOINT:?}"
: "${BACKUP_S3_BUCKET:?}"
: "${BACKUP_S3_KEY:?}"
: "${BACKUP_S3_SECRET:?}"
: "${BACKUP_AGE_RECIPIENT:?media backup requires BACKUP_AGE_RECIPIENT (encryption is mandatory)}"

MEDIA_ROOT="/srv/memory/data"
# Space-separated dir names under MEDIA_ROOT. Adjust as new media types appear.
# Default matches .env.example; `bot_voice` (the bot's captured media) was being
# dropped when the default said `photos`.
MEDIA_DIRS="${BACKUP_MEDIA_DIRS:-voice bot_voice}"

export AWS_ACCESS_KEY_ID="$BACKUP_S3_KEY" AWS_SECRET_ACCESS_KEY="$BACKUP_S3_SECRET"
aws_s3() { aws --endpoint-url "$BACKUP_S3_ENDPOINT" "$@"; }

echo "[$(date -u +%FT%TZ)] media backup starting (dirs: $MEDIA_DIRS)"

# One listing of everything already under media/ — cheaper than a HEAD per file.
declare -A HAVE=()
while IFS= read -r k; do
  [[ -n "$k" && "$k" != "None" ]] && HAVE["$k"]=1
done < <(aws_s3 s3api list-objects-v2 --bucket "$BACKUP_S3_BUCKET" --prefix "media/" \
           --query 'Contents[].Key' --output text 2>/dev/null | tr '\t' '\n')

new=0
for d in $MEDIA_DIRS; do
  src="$MEDIA_ROOT/$d"
  if [[ ! -d "$src" ]]; then
    echo "[$(date -u +%FT%TZ)] skip (missing): $src"
    continue
  fi
  while IFS= read -r -d '' f; do
    rel="${f#"$MEDIA_ROOT"/}"          # e.g. voice/2026/06/note.ogg
    key="media/${rel}.age"
    [[ -n "${HAVE[$key]:-}" ]] && continue   # already backed up; immutable, skip
    # Stream: encrypt to stdout, pipe straight to S3 — no plaintext temp file.
    if age -r "$BACKUP_AGE_RECIPIENT" "$f" \
         | aws_s3 s3 cp - "s3://$BACKUP_S3_BUCKET/$key" --no-progress >/dev/null; then
      new=$((new + 1))
    else
      echo "[$(date -u +%FT%TZ)] WARNING: failed to upload $rel — will retry next run"
    fi
  done < <(find "$src" -type f -print0)
done

unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY
echo "[$(date -u +%FT%TZ)] media backup done: $new new file(s) uploaded"
