# Mail header backfill drain

Why: ~5.5k messages ingested before Phase B (2026-06-29) have no
`payload['headers']`, so triage signals (auto/bulk/list chips) and unsubscribe
links are missing on old mail. `POST /api/mail/backfill-headers` already
enriches 500/run (lazy on thread open + a manual endpoint); this timer just
drains the whole backlog — and keeps draining anything new (bulk imports,
re-added accounts) forever. Once drained, each run is one indexed no-op query
(`WHERE NOT payload ? 'headers'`), zero Gmail quota.

Cadence: every 20 min × 500 msgs ⇒ ~5.5k drained in ~4h. While draining, each
run costs ~2500 Gmail quota units (500 × messages.get) — far under the
per-user per-minute cap.

## Install (droplet)

```bash
sudo cp deploy/mail_backfill/memory-mail-backfill.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now memory-mail-backfill.timer
# first run now + watch it:
sudo systemctl start memory-mail-backfill.service
tail -f /srv/memory/logs/mail_backfill.log
```

Then give the new unit the standard failure alert (see deploy/alerting/README.md —
"new units added later need the same drop-in"):

```bash
sudo mkdir -p /etc/systemd/system/memory-mail-backfill.service.d
printf '[Unit]\nOnFailure=memory-notify-fail@%%p.service\n' \
  | sudo tee /etc/systemd/system/memory-mail-backfill.service.d/onfailure.conf >/dev/null
sudo systemctl daemon-reload
```

## Monitor the drain

```sql
SELECT count(*) FROM raw.gmail_message m
  JOIN raw.gmail_account a
    ON a.email = m.account_email
   AND a.status = 'active' AND a.refresh_token IS NOT NULL
 WHERE NOT (m.payload ? 'headers');
```

Expect ~500 fewer per 20 min → 0. Leave the timer enabled afterwards.
