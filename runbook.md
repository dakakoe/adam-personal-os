# runbook.md

Incident response for the memory droplet. Add notes here every time you hit
something the existing entries don't cover.

## "I can't SSH in"

1. Try the public IP path (works only if port 22 is still open at the firewall):
   `ssh root@<reserved-ip>`
2. Try via DigitalOcean web console:
   DO dashboard → Droplets → memory → Console → log in as root.
   - `ufw status` — is 22 allowed?
   - `systemctl status ssh` — is sshd running?
   - `systemctl status tailscaled` — is Tailscale up?
   - `tailscale status` — is the node connected?
3. If Tailscale is dead but ssh is fine, reopen port 22 temporarily via DO
   Cloud Firewall (Networking → Firewalls → memory-fw → add inbound TCP/22)
   and SSH in to debug.

## "Postgres won't start"

1. Check disk space — Postgres refuses to start on a full disk:
   `df -h /srv/memory`
2. Check container logs:
   `docker compose logs --tail=200 postgres`
3. If WAL is corrupt, last resort is restore from backup:
   - Stop the stack: `docker compose down`
   - Move broken data: `sudo mv /srv/memory/data/postgres /srv/memory/data/postgres.broken`
   - Restore latest dump per `scripts/restore.sh` (after recreating the DB)

## "Disk filling up"

```bash
ssh memory
ncdu /srv/memory
```

Common culprits:
- `/srv/memory/data/postgres/pg_wal` — runaway WAL, usually means a replication slot
  or `wal_keep_size` misconfigured
- `/srv/memory/backups/` — backup retention not pruning (check `scripts/backup.sh`)
- `/srv/memory/logs/` — log rotation broken (check `/etc/logrotate.d/memory`)

## "TLS certificate failed"

Caddy auto-renews via Let's Encrypt. If it fails:

```bash
docker compose logs caddy | grep -i "obtain\|certificate\|acme"
```

Common causes:
- DNS A record changed and Caddy hasn't reloaded
- Port 80 blocked by firewall (Let's Encrypt HTTP-01 challenge needs it)
- Rate-limit from Let's Encrypt — wait an hour

Force a reload:
```bash
docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile
```

## "Telegram session locked"

Handled in the application-layer document. The short version:
- Stop the telethon service: `sudo systemctl stop memory-telethon`
- Investigate the `.session` file lock
- Restart only when you know why it died (avoid lockout from Telegram)

## "Whisper is eating all the CPU"

Should not happen — the systemd unit uses `Nice=15` and `IOSchedulingClass=idle`.
If it does:
```bash
sudo systemctl stop memory-whisper
top  # confirm CPU returns to baseline
sudo systemctl status memory-whisper
journalctl -u memory-whisper --since "1 hour ago"
```

## "I rotated a secret, what do I update?"

1. Update `/srv/memory/secrets/.env` on the droplet
2. Restart the dependent services:
   - Postgres password: `docker compose down && docker compose up -d`
     (and update the password inside Postgres if you changed it)
   - Anthropic key: `sudo systemctl restart memory-merge-api memory-mcp`
3. Re-run a backup to confirm everything still has access:
   `bash /srv/memory/scripts/backup.sh`
