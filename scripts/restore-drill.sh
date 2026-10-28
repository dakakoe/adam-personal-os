#!/usr/bin/env bash
# Automated restore drill — proves a backup is actually restorable.
# Run via memory-restore-drill.timer (weekly). Restores the newest LOCAL dump
# into a throwaway DB, sanity-checks it, drops it, and alerts on ANY failure.
#
# Why local (not the .age in B2): the off-site copy is age-encrypted and can
# only be decrypted with the private key, which is OFF the droplet by design.
# So this drill validates dump integrity + restore-ability here; the ENCRYPTED
# off-site round-trip (real key + B2 object) is proven by scripts/verify-offsite.sh,
# run periodically on the machine that holds the age key. Run that too.
#
# NOTE: not `set -e` — we want to catch failures and alert, not die silently.
set -uo pipefail

# shellcheck disable=SC1091
source /srv/memory/secrets/.env

: "${POSTGRES_USER:?}"

TEST_DB="memory_restore_test"
DC=(docker compose -f /srv/memory/docker-compose.yml --env-file /srv/memory/secrets/.env)

alert() {
  local msg="$1"
  echo "[$(date -u +%FT%TZ)] ALERT: $msg"
  if [[ -n "${ALERT_TG_BOT_TOKEN:-}" && -n "${ALERT_TG_CHAT_ID:-}" ]]; then
    curl -sS --max-time 15 \
      "https://api.telegram.org/bot${ALERT_TG_BOT_TOKEN}/sendMessage" \
      --data-urlencode "chat_id=${ALERT_TG_CHAT_ID}" \
      --data-urlencode "text=🔴 restore-drill: ${msg}" >/dev/null || true
  fi
}

DUMP=$(ls -t /srv/memory/backups/memory-*.sql.gz 2>/dev/null | head -1)
if [[ -z "$DUMP" ]]; then
  alert "no local dump found in /srv/memory/backups to drill"
  exit 1
fi
echo "[$(date -u +%FT%TZ)] drilling $(basename "$DUMP")"

# Restore into the throwaway DB via the shared restore script.
if ! bash /srv/memory/scripts/restore.sh "$DUMP" >/tmp/restore-drill.out 2>&1; then
  alert "restore FAILED for $(basename "$DUMP") (see /tmp/restore-drill.out on the droplet)"
  "${DC[@]}" exec -T postgres dropdb -U "$POSTGRES_USER" --if-exists "$TEST_DB" >/dev/null 2>&1
  exit 1
fi

# Sanity check: a core ingest table should have rows after a good restore.
CNT=$("${DC[@]}" exec -T postgres psql -U "$POSTGRES_USER" "$TEST_DB" -tAc \
  "SELECT count(*) FROM raw.telegram_message" 2>/dev/null | tr -d '[:space:]')

RC=0
if [[ -z "$CNT" || "$CNT" == "0" ]]; then
  alert "restored OK but sanity query returned '${CNT:-<none>}' rows (expected > 0) for $(basename "$DUMP")"
  RC=1
else
  echo "[$(date -u +%FT%TZ)] drill OK: restored, raw.telegram_message=$CNT rows"
fi

# Always drop the throwaway DB.
"${DC[@]}" exec -T postgres dropdb -U "$POSTGRES_USER" --if-exists "$TEST_DB" >/dev/null 2>&1
exit $RC
