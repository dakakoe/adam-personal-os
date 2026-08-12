# INFRASTRUCTURE.md

> **Status (2026-07): historical deep reference.** This was the original
> phased build-out guide and its low-level detail (bootstrap, Docker stack,
> Caddy/Authelia, systemd conventions) is still accurate. For a NEW install,
> start from [`FRIEND_INSTALL.md`](./FRIEND_INSTALL.md) instead. Known drift:
> **Twenty CRM was decommissioned 2026-07-02 — skip every Twenty step**, and
> services added later (rspamd, ollama, mail_ml, the mail client, alerting,
> the `/setup` wizard) are documented in `deploy/*/README.md`.

**Purpose:** Step-by-step instructions for Claude Code to provision and configure the infrastructure for the personal AI-memory CRM system.
**Owner:** You (the human). Claude Code executes these phases under your supervision.
**Read this entire document before executing anything.** Each phase has a `STOP-AND-VERIFY` checkpoint. Do not proceed past a checkpoint without the human confirming success.

---

## 0. Project Overview

We are building a personal contact-memory system with these components:

- **Raw event store** — Postgres, append-only tables of ingested messages
- **Canonical store** — Postgres, deduplicated contacts and normalized interactions
- **AI memory layer** — Postgres + pgvector, profiles and embeddings
- **Telethon listener** — long-running Python process ingesting Telegram
- **Whisper transcription** — local CPU-based voice transcription queue
- **MCP server** — exposes contact memory as tools for Claude clients
- **Daily digest** — cron-driven email summary

All of this runs on a single DigitalOcean droplet, with Claude Code installed on the droplet for ongoing development under restricted privileges.

---

## 1. Defaults

| Setting | Value | Notes |
|---|---|---|
| Cloud provider | DigitalOcean | Has $200 free credit; Singapore region |
| Region | `sgp1` | Closest to user's location (your city, TH) |
| Droplet plan | Basic Premium Intel | `s-4vcpu-8gb-intel` — $48/mo |
| OS | Ubuntu 24.04 LTS | x86_64 |
| Backups | Enabled | +20% of droplet cost (~$9.60/mo) |
| Reserved IP | Yes | Free while attached |
| Hostname | `memory` | Used in DNS and Tailscale |
| Non-root user | `ops` | Sudoer, used for human ops |
| Claude Code user | `code` | No sudo, no access to secrets/session |
| Database | Postgres 16 + pgvector | In Docker, persistent volume |
| Reverse proxy | Caddy | Automatic Let's Encrypt for the domain |
| Private networking | Tailscale | Default reachability path |
| Public ports | 80, 443 only | Everything else via tailnet |

**Project-specific values (committed by the human):**

| Setting | Value |
|---|---|
| Apex domain | `example.com` |
| MCP host | `memory.example.com` |
| Twenty CRM host | `crm.example.com` |
| Let's Encrypt email | `admin@example.com` |
| Local SSH key | `~/.ssh/id_ed25519` |

**Sizing note:** The 4 vCPU / 8 GB tier ($48/mo) is recommended because Whisper transcription on the same box will consume CPU during backfill. If you want to start cheaper, the 2 vCPU / 4 GB tier ($24/mo) works but will feel tight; you can resize up later (DO supports CPU/RAM resizing without disk loss).

---

## 2. Repo Structure (Local, On Your Laptop)

This repo. Final structure:

```
memory/
├── INFRASTRUCTURE.md          # this file
├── README.md                  # short project description
├── runbook.md                 # incident response
├── .env.example               # template, committed
├── .env                       # never committed; in .gitignore
├── .gitignore
├── infra/
│   ├── doctl-create.sh        # droplet provisioning
│   ├── bootstrap.sh           # runs on droplet as root, one-time
│   ├── caddy/Caddyfile        # reverse proxy config
│   ├── docker-compose.yml     # postgres + twenty + caddy stack
│   └── systemd/               # unit files for telethon, mcp, digest, whisper, backup
├── migrations/                # SQL migration files, numbered
├── fetchers/
│   └── telegram/              # Telethon listener (built in later phase)
├── mcp_server/                # MCP server (built in later phase)
├── digest/                    # daily digest job (built in later phase)
├── whisper_worker/            # transcription queue worker
└── scripts/
    ├── backup.sh              # pg_dump to off-droplet location
    └── restore.sh             # rebuild from dump
```

`.gitignore` includes at minimum: `.env`, `*.session`, `*.session-journal`, `secrets/`, `data/`.

---

## 3. Phase 0 — Pre-flight (Your Laptop)

**Goal:** Have the local tools and credentials ready before touching DigitalOcean.

### 3.1 Install local tools

```bash
# macOS
brew install doctl tailscale jq
```

### 3.2 Generate a dedicated SSH key

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -C "memory-droplet-mac"
```

Add to `~/.ssh/config`:

```
Host memory
  HostName <reserved-ip-fills-in-later>
  User ops
  IdentityFile ~/.ssh/id_ed25519
  IdentitiesOnly yes
```

If you set a passphrase, store it in macOS Keychain:

```bash
ssh-add --apple-use-keychain ~/.ssh/id_ed25519
```

### 3.3 DO API token

1. https://cloud.digitalocean.com/account/api/tokens
2. Generate new token, **read + write**, name `memory-provisioning`
3. Save in `~/.config/memory/secrets.env`:

```bash
mkdir -p ~/.config/memory && chmod 700 ~/.config/memory
nano ~/.config/memory/secrets.env
# Paste:
#   DO_TOKEN=dop_v1_xxxxx
#   TS_AUTHKEY=tskey-auth-xxxxx
chmod 600 ~/.config/memory/secrets.env
```

Authenticate `doctl`:

```bash
source ~/.config/memory/secrets.env
doctl auth init -t "$DO_TOKEN"
doctl account get
```

### 3.4 Upload SSH key to DO

```bash
doctl compute ssh-key import id_ed25519 --public-key-file ~/.ssh/id_ed25519.pub
doctl compute ssh-key list
```

### 3.5 Tailscale auth key

1. https://login.tailscale.com/admin/settings/keys
2. Generate: **reusable: off, ephemeral: off, pre-approved: on**
3. Save in `~/.config/memory/secrets.env` as `TS_AUTHKEY=tskey-auth-xxxxx`

### 3.6 DNS — wait until Phase 1

Don't create records yet; you'll add them once the Reserved IP is assigned.

### 3.7 STOP-AND-VERIFY

- [ ] `doctl account get` returns valid account info
- [ ] `~/.ssh/id_ed25519` exists and is uploaded to DO
- [ ] `~/.config/memory/secrets.env` contains `DO_TOKEN` and `TS_AUTHKEY`
- [ ] Domain ownership and DNS provider access confirmed
- [ ] `.env.example` exists; `.env` in `.gitignore`

---

## 4. Phase 1 — Droplet Provisioning

**Goal:** Running Ubuntu 24.04 droplet with Reserved IP and Cloud Firewall.

### 4.1 Run the provisioning script

```bash
source ~/.config/memory/secrets.env
bash infra/doctl-create.sh
```

Script does:
- Picks the SSH key ID
- Creates `memory` droplet in `sgp1`, size `s-4vcpu-8gb-intel`
- Reserves a static IP and attaches it
- Creates Cloud Firewall `memory-fw` (22, 80, 443 inbound)
- Prints the reserved IP for the next step

### 4.2 Update `~/.ssh/config`

Replace `<reserved-ip-fills-in-later>` with the IP the script printed.

### 4.3 Add DNS records (GoDaddy)

GoDaddy → DNS for `example.com` → Add records:

- A · `memory` · `<RESERVED_IP>` · TTL 600
- A · `crm` · `<RESERVED_IP>` · TTL 600

Verify:

```bash
dig +short memory.example.com
dig +short crm.example.com
```

### 4.4 First SSH

```bash
ssh root@<RESERVED_IP>
```

### 4.5 STOP-AND-VERIFY

- [ ] `ssh root@<reserved-ip>` succeeds
- [ ] Both DNS records resolve to the reserved IP
- [ ] Firewall `memory-fw` lists the droplet
- [ ] Backups enabled in DO dashboard

---

## 5. Phase 2 — Droplet Hardening

**Goal:** Locked-down host, key-only SSH, Tailscale up, public SSH closed.

### 5.1 Run the bootstrap script

```bash
source ~/.config/memory/secrets.env
RESERVED_IP=$(doctl compute reserved-ip list --no-header --format IP | head -1)

scp -i ~/.ssh/id_ed25519 infra/bootstrap.sh root@$RESERVED_IP:/root/bootstrap.sh

# Heredoc form — preserves spaces in the public key value.
# Do NOT try `ssh ... VAR="$(cat key.pub)" bash ...` — ssh concatenates args
# with spaces and the public key's internal spaces break remote parsing.
ssh -i ~/.ssh/id_ed25519 root@$RESERVED_IP bash -s <<EOF
export TS_AUTHKEY="$TS_AUTHKEY"
export OPS_PUBKEY="$(cat ~/.ssh/id_ed25519.pub)"
bash /root/bootstrap.sh
EOF

ssh -i ~/.ssh/id_ed25519 root@$RESERVED_IP rm /root/bootstrap.sh
```

### 5.2 Test SSH via Tailscale

Make sure Tailscale is running on your Mac (status bar icon → connected). Then:

```bash
ssh ops@memory   # uses Tailscale MagicDNS
sudo whoami      # should print 'root' with no password
```

### 5.3 Close public SSH

Only after the Tailscale path works:

```bash
ssh memory 'sudo ufw delete allow 22/tcp'

# Get firewall ID from laptop
FW_ID=$(doctl compute firewall list --no-header --format ID,Name | awk '$2=="memory-fw"{print $1}')

doctl compute firewall update "$FW_ID" \
  --inbound-rules "protocol:tcp,ports:80,address:0.0.0.0/0,address:::/0 protocol:tcp,ports:443,address:0.0.0.0/0,address:::/0" \
  --outbound-rules "protocol:tcp,ports:all,address:0.0.0.0/0,address:::/0 protocol:udp,ports:all,address:0.0.0.0/0,address:::/0 protocol:icmp,address:0.0.0.0/0,address:::/0"
```

Update `~/.ssh/config`:

```
Host memory
  HostName memory    # Tailscale MagicDNS
  User ops
  IdentityFile ~/.ssh/id_ed25519
```

### 5.4 STOP-AND-VERIFY

- [ ] `ssh memory` works as `ops` over Tailscale
- [ ] `ssh root@<reserved-ip>` from non-tailnet network **fails**
- [ ] `sudo` works without password for `ops`
- [ ] `ufw status` shows only 80, 443, tailscale0
- [ ] fail2ban running
- [ ] Droplet visible in Tailscale admin console

**If you can no longer SSH in:** DO dashboard → Firewalls → memory-fw → temporarily re-add inbound 22.

---

## 6. Phase 3 — Base Services (Docker, Postgres, Caddy, Twenty)

All steps as `ops` on the droplet.

### 6.1 Install Docker

```bash
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker ops
exit  # log out, log back in for group change
```

After re-login: `docker run --rm hello-world`

### 6.2 Directory layout

```bash
sudo mkdir -p /srv/memory/{data/postgres,data/twenty,data/caddy,data/caddy-config,secrets,logs,backups,apps}
sudo chown -R ops:ops /srv/memory
chmod 700 /srv/memory/secrets
```

### 6.3 Push config to droplet

From laptop:

```bash
# Fill out a real .env first from .env.example
cp .env.example .env
# edit .env with: openssl rand -base64 32 for the two secrets

scp .env                       memory:/srv/memory/secrets/.env
scp infra/docker-compose.yml   memory:/srv/memory/docker-compose.yml
scp infra/caddy/Caddyfile      memory:/srv/memory/Caddyfile
ssh memory chmod 600 /srv/memory/secrets/.env
```

### 6.4 Create the twenty database

```bash
ssh memory
cd /srv/memory
docker compose --env-file secrets/.env up -d postgres
sleep 10
docker compose --env-file secrets/.env exec postgres \
  psql -U memory -c "CREATE DATABASE twenty;"
docker compose --env-file secrets/.env exec postgres \
  psql -U memory -d memory -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

### 6.5 Bring up the stack

```bash
docker compose --env-file secrets/.env up -d
docker compose ps
```

### 6.6 Verify from laptop

```bash
curl -I https://crm.example.com     # 200 or redirect to Twenty
curl -I https://memory.example.com  # 503 (placeholder)
```

Open `https://crm.example.com` and complete Twenty's initial admin setup.

### 6.7 STOP-AND-VERIFY

- [ ] Postgres container healthy
- [ ] `pgvector` extension installed in `memory` DB
- [ ] Twenty CRM reachable with valid TLS
- [ ] `memory.example.com` returns 503 with valid TLS
- [ ] Initial Twenty admin account created and login works

---

## 7. Phase 4 — Application Stack Scaffolding

**Goal:** Directories, systemd unit files, no application code yet.

### 7.1 Install Python and uv

```bash
ssh memory
sudo apt-get install -y python3.12 python3.12-venv python3-pip ffmpeg build-essential
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 7.2 Push systemd units

From laptop:

```bash
scp infra/systemd/*.service infra/systemd/*.timer memory:/tmp/
ssh memory '
  sudo mv /tmp/memory-*.service /tmp/memory-*.timer /etc/systemd/system/
  sudo systemctl daemon-reload
'
```

Do NOT enable the services yet — no code to run.

### 7.3 Log rotation

```bash
ssh memory 'sudo tee /etc/logrotate.d/memory > /dev/null' <<'EOF'
/srv/memory/logs/*.log {
    daily
    rotate 14
    compress
    delaycompress
    notifempty
    missingok
    create 0640 ops ops
}
EOF
```

### 7.4 STOP-AND-VERIFY

- [ ] App directories exist under `/srv/memory/apps/`
- [ ] `systemctl list-unit-files | grep memory` lists all units
- [ ] None enabled or started
- [ ] `logrotate` config exists

---

## 8. Phase 5 — Database Schema

**Goal:** Migration framework in place. Actual schema is a separate design pass.

### 8.1 Install dbmate

```bash
ssh memory
sudo curl -fsSL -o /usr/local/bin/dbmate \
  https://github.com/amacneil/dbmate/releases/latest/download/dbmate-linux-amd64
sudo chmod +x /usr/local/bin/dbmate
```

### 8.2 First migration

```bash
cd /srv/memory
source secrets/.env
export DATABASE_URL="postgres://memory:${POSTGRES_PASSWORD}@localhost:5432/memory?sslmode=disable"
dbmate new init_schema
# Edit the generated file to add a placeholder table; the real schema is its own document.
dbmate up
```

### 8.3 STOP-AND-VERIFY

- [ ] `dbmate up` runs without error
- [ ] Placeholder table exists
- [ ] `db/migrations/` directory committed

---

## 9. Phase 6 — Claude Code on the Droplet (Restricted)

**Goal:** Claude Code installed under the `code` user with explicit restrictions.

### 9.1 Install Node + Claude Code

```bash
ssh memory
sudo -iu code bash -c '
  curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
'
sudo apt-get install -y nodejs
sudo -iu code npm install -g @anthropic-ai/claude-code
```

### 9.2 Permissions

```bash
sudo mkdir -p /srv/memory/code-workspace
sudo chown code:code /srv/memory/code-workspace

# Allow code to read app source, not secrets or data
sudo setfacl -R -m u:code:rwx /srv/memory/apps
sudo setfacl    -m u:code:r-- /srv/memory/Caddyfile /srv/memory/docker-compose.yml
sudo setfacl -R -m u:code:--- /srv/memory/secrets
sudo setfacl -R -m u:code:--- /srv/memory/data
```

### 9.3 Sudoers allowlist

`/etc/sudoers.d/code-restart`:

```
code ALL=(root) NOPASSWD: /bin/systemctl restart memory-telethon, /bin/systemctl restart memory-mcp, /bin/systemctl restart memory-whisper, /bin/systemctl restart memory-digest, /bin/systemctl status memory-*, /bin/journalctl -u memory-*
```

```bash
sudo chmod 0440 /etc/sudoers.d/code-restart
sudo visudo -c
```

### 9.4 Sync the repo

From laptop:

```bash
rsync -av --exclude='.env' --exclude='.git' --exclude='data/' \
  ./ code@memory:/srv/memory/code-workspace/
```

### 9.5 STOP-AND-VERIFY

- [ ] `sudo -u code cat /srv/memory/secrets/.env` → Permission denied
- [ ] `sudo -u code ls /srv/memory/data/postgres` → Permission denied
- [ ] `sudo -u code sudo systemctl restart memory-telethon` succeeds (or fails because no code, but is allowed)
- [ ] `sudo -u code sudo systemctl restart caddy` → not in allowlist, fails
- [ ] Claude Code authenticates as `code`

---

## 10. Phase 7 — Backups and Observability

### 10.1 Off-droplet backup destination

Backblaze B2 recommended. Create bucket `memory-backups`, generate an app key with write-only access, fill in the `BACKUP_S3_*` keys in `/srv/memory/secrets/.env`.

### 10.2 Enable the backup timer

```bash
ssh memory
sudo systemctl enable --now memory-backup.timer
systemctl list-timers memory-backup.timer
```

### 10.3 Restore drill

```bash
ssh memory
# Pull latest from B2 into a tmp file first
bash /srv/memory/scripts/restore.sh /srv/memory/backups/memory-<timestamp>.sql.gz
```

If the throwaway DB restores cleanly, you have real disaster recovery.

### 10.4 Monitoring

DO dashboard → Monitoring → set alerts:
- CPU > 80% for 5 min
- Memory > 90% for 5 min
- Disk > 80%
- Droplet unreachable for 5 min

Plus an external uptime check (Uptime Robot or Better Stack) pinging:
- `https://crm.example.com`
- `https://memory.example.com`

### 10.5 STOP-AND-VERIFY

- [ ] `scripts/backup.sh` runs and uploads to B2
- [ ] Backup timer scheduled
- [ ] Restore drill passed
- [ ] DO monitoring alerts configured
- [ ] External uptime monitor configured

---

## 11. Out of Scope Here

- Telethon fetcher implementation
- Whisper worker implementation
- MCP server implementation
- Daily digest content and email delivery
- Database schema for raw/canonical/AI-memory tables
- Twenty CRM custom objects and field mapping
- Multi-source ingestion (Gmail, LinkedIn, WhatsApp)

Each gets its own design conversation and its own follow-up document.

---

## 12. Final Checklist Before Building Apps

- [ ] Droplet reachable only via Tailscale (public SSH closed)
- [ ] Both subdomains serve valid HTTPS via Caddy
- [ ] Postgres + pgvector running, not publicly exposed
- [ ] Twenty CRM running and login works
- [ ] Claude Code installed as `code` user with no secret access
- [ ] Daily backup to B2 working, restore drill passed
- [ ] Monitoring + external uptime configured
- [ ] All systemd units staged but not enabled

When all boxes are checked, infrastructure is ready. Next conversation is schema design.
