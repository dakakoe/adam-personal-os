# Scheduled spam scan (Mail auto-triage Phase B2)

A systemd timer that auto-runs the Rspamd spam scan so the inbox stays flagged
without clicking "Scan spam". Curls `POST /api/mail/scan-spam` (bearer auth) —
no separate venv; the scoring lives in merge_api.

## Install (on the droplet)
```
sudo cp deploy/mail_spam/memory-mail-spam.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now memory-mail-spam.timer
```
Depends on the Rspamd service (see `deploy/rspamd/`) being up.
