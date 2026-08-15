# Unit-failure alerting (Personal OS)

Why: on Jun 24 a deploy rsynced `backup-media.sh`/`restore-drill.sh` without the
exec bit; both weekly sovereignty units died with 203/EXEC and **nobody knew for
9 days**. This makes any `memory-*` unit failure ping Telegram immediately.

## Pieces
- `memory-notify-fail@.service` — template unit; `%i` is the failed unit's name.
  Sends via the alerter's existing bot creds (`ALERT_TG_BOT_TOKEN`/`ALERT_TG_CHAT_ID`).
- A **drop-in** per `memory-*` unit: `/etc/systemd/system/<unit>.d/onfailure.conf`
  with `OnFailure=memory-notify-fail@%n.service`. Drop-ins survive unit-file
  replacements (the failure mode that started this).

## Install (droplet)
```bash
sudo cp deploy/alerting/memory-notify-fail@.service /etc/systemd/system/
for u in $(systemctl list-unit-files "memory-*.service" --no-legend | awk '{print $1}' | grep -v notify-fail); do
  sudo mkdir -p /etc/systemd/system/$u.d
  printf '[Unit]\nOnFailure=memory-notify-fail@%%p.service\n' | sudo tee /etc/systemd/system/$u.d/onfailure.conf >/dev/null
done
sudo systemctl daemon-reload
# test: should ping Telegram
sudo systemctl start memory-notify-fail@TEST.service
```
New units added later need the same drop-in (re-run the loop).
