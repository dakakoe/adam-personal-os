# Install your own instance (friend install)

This repo is a self-hosted **Personal OS**: your mail, Telegram, contacts,
meetings, tasks, deals, and budget on your own server — with local-LLM
privacy routing and a web UI. This page gets a NEW person from zero to a
running instance, then the in-app **Setup wizard** (`/setup`) connects your
accounts — no SSH archaeology after the base install.

> Status: first supported friend-install path (Phase 6 groundwork). It has
> been assembled from the live production install; expect to supervise
> Claude and answer its questions rather than walk away.

## What you need before starting

| Thing | Why | Where |
|---|---|---|
| Access to this repo | it's private | ask the owner for a collaborator invite, or a fine-grained read-only PAT |
| A server (see sizing below) | everything runs on one box | DigitalOcean/Hetzner/anything Ubuntu 22.04+ |
| A domain (or subdomain) | HTTPS UI via Caddy + Authelia | any registrar |
| Google Cloud OAuth client | Gmail/Calendar/Contacts ingest | console.cloud.google.com → create a project → OAuth client. You need BOTH a "Desktop app" client (`credentials.json`, used by workers) and the web redirect URI for in-UI consent (`MERGE_OAUTH_REDIRECT_URI=https://<your-domain>/api/sources/oauth/callback`) |
| Telegram API credentials | live chat ingest | my.telegram.org → API development tools (`api_id` + `api_hash`) |
| Tailscale account (recommended) | SSH without exposing :22 | tailscale.com, free tier is fine |
| Anthropic API key (optional) | capture extraction, planner, profiles for non-sensitive contacts | console.anthropic.com — most features degrade gracefully without it; the digest and sensitive-contact paths are local-LLM anyway |
| Telegram bot token (optional) | the capture bot + failure alerts | @BotFather |
| Granola API key (optional) | meeting recaps | pasted later in the Setup wizard, not during install |

## Server sizing

Numbers from the production instance (≈500k messages, 12k contacts, 30k
emails, all features on):

| Tier | Specs | What you get | ~cost |
|---|---|---|---|
| Minimum | 2 vCPU / 4 GB / 60 GB SSD | core system without the local-LLM stack: no Ollama, no SetFit mail classifier, no local embeddings backlog comfort. Works, but privacy routing is off | ~$24/mo |
| **Recommended** | **4 vCPU / 8 GB / 80–160 GB SSD** | everything, as run in production: Postgres+pgvector, Ollama (your chosen 3B model, 3.5 GB cap), SetFit classifier + weekly retrain (3.6 GB peak), embeddings, ~40 systemd units | ~$48/mo |
| Comfortable | 8 vCPU / 16 GB / 160 GB | headroom for a bigger local model (7–8B), faster drafts/digests, heavy mail volume | ~$96/mo |

Disk reality check: production uses ~41 GB total after a year of data
(DB + media + models + backups). CPU-only inference is fine for background
jobs; interactive local drafts take 30–90s at 4 vCPU.

Local model: **you choose one at install** — nothing is bundled. The installer
recommends by your RAM and pulls it; `OLLAMA_MODEL` in `.env` names your pick
and every service inherits it. See `deploy/ollama/README.md` for the full
table. Short version: 8 GB → `llama3.2:3b` (permissive license, the default) or
`qwen2.5:3b` (stronger multilingual, research license); 16 GB+ → a 7–8B model;
under 8 GB → skip it (cloud-only).

## How to install

1. Get repo access and a server (bare Ubuntu 22.04+, root SSH).
2. Install [Claude Code](https://claude.com/claude-code) on your laptop.
3. Clone the repo locally, `cd` into it, run `claude`, and paste the prompt
   below. Answer its questions; approve each phase.

---

## The install prompt (paste into Claude Code)

```text
You are installing my self-hosted Personal OS from this repository onto my
own fresh server. Work phase by phase, STOP at each checkpoint and let me
verify before continuing. Never invent values for secrets — ask me. Never
commit secrets to git.

CONTEXT
- This repo runs in production on a 4 vCPU / 8 GB Ubuntu droplet. Everything
  lives under /srv/memory on the server: apps/ (one dir per service, each
  with its own .venv), migrations/ (dbmate), scripts/, secrets/.env (the
  single env file all systemd units read), data/, logs/, docker-compose.yml
  (+ override).
- Read these BEFORE acting, in order: README.md, INFRASTRUCTURE.md (the
  original phased install guide — mostly still accurate), runbook.md,
  infra/ (bootstrap.sh, docker-compose.yml, caddy/, authelia/, systemd/ — 41
  unit files), deploy/ (per-feature units + READMEs: ollama, rspamd,
  mail_ml, mail_spam, mail_backfill, alerting), SCHEMA.md.
- IMPORTANT corrections to INFRASTRUCTURE.md (it predates later phases):
  Twenty CRM is DECOMMISSIONED — skip every Twenty step. Services added
  since it was written: rspamd (spam), ollama (local LLM), mail_ml (SetFit
  classifier), the mail client, unified alerting (OnFailure drop-ins →
  Telegram), and the /setup wizard. Their install notes live in deploy/*/README.md.

MY INPUTS (ask me for each when you need it; I have them ready)
- server IP + root SSH access; my SSH public key
- domain name for the UI
- Tailscale auth key (or I'll choose to keep plain SSH — ask)
- Google OAuth: credentials.json (Desktop app client) + web client redirect
  URI configured as https://<domain>/api/sources/oauth/callback
- Telegram api_id + api_hash (+ my phone number, used later in the wizard)
- optional: Anthropic API key, Telegram bot token + chat id for alerts

PHASES (checkpoint after each)
1. Provision + harden: run infra/bootstrap.sh on the server per its header
   comment (creates the `ops` user, UFW, fail2ban, Tailscale, Docker). All
   later work happens as `ops`.
2. Base stack: /srv/memory skeleton; docker compose up postgres + redis +
   caddy + authelia from infra/docker-compose.yml; configure Caddy for my
   domain from infra/Caddyfile.merge.snippet and Authelia from infra/authelia/
   (generate my user + TOTP). Checkpoint: I can log in at https://<domain>.
3. Secrets: build /srv/memory/secrets/.env — use .env.example +
   scripts/build-env.sh as the base, then walk me through every remaining
   key it needs (grep the systemd units in infra/systemd/ and deploy/ for
   env names; anything ALERT_TG_* / ANTHROPIC_* / GRANOLA_* is optional and
   the system degrades gracefully without it).
4. Database: run migrations with scripts/dbmate.sh (dbmate up applies
   migrations/ in order; pgvector extension comes from the migrations).
   ALWAYS migrations before starting services.
5. Services: rsync each app dir into /srv/memory/apps/<name>/, create its
   .venv (uv sync or pip install -e .; torch-based ones — mail_ml,
   embedding_worker, profile_builder, whisper — need the CPU wheel index,
   see deploy/mail_ml/README.md), chmod +x any shell scripts BEFORE rsync,
   install the systemd units from infra/systemd/ + deploy/*/, daemon-reload,
   enable timers. Install the alerting drop-ins per deploy/alerting/README.md
   (skip if no bot token). Build the UI: cd apps/merge_ui && npm install &&
   npm run build; start memory-merge-api + memory-merge-ui.
6. Local LLM — GUIDED CHOICE: check my server's RAM, then present me the model
   options from deploy/ollama/README.md's table (under 8 GB → recommend
   skipping; 8 GB → llama3.2:3b [permissive, default] or qwen2.5:3b [research
   license, stronger multilingual]; 16 GB+ → a 7–8B). State the license for
   each and let me pick. Then: add the ollama service, set OLLAMA_MODEL=<my
   pick> in /srv/memory/secrets/.env, `ollama pull "$OLLAMA_MODEL"`, verify
   curl 127.0.0.1:11434/api/tags. If I skip, leave OLLAMA_MODEL blank and don't
   install ollama/mail_ml (their features degrade gracefully). Same
   container-pattern for rspamd per deploy/rspamd/ (data dir must be chowned to
   container uid 11333).
7. Verify: every memory-* unit active or cleanly scheduled (list-timers),
   /api/sources returns all sources, the UI loads.
8. Hand off to the app: send me to https://<domain>/setup — the in-app
   wizard connects Google accounts (OAuth in the browser), Telegram
   (phone → code → 2FA in the UI), Granola (paste key), LinkedIn (upload
   export). Nothing after this point needs SSH.

GOTCHAS (learned in production — respect them)
- rsync must preserve exec bits; a dropped +x on a script once killed
  backups silently for 9 days (that's why alerting exists).
- dbmate BEFORE service restarts, always.
- The telethon session file is auth-token-equivalent: 0600, owned by ops,
  never in git, never in world-readable backups.
- Ollama serializes concurrent requests — generous timeouts in callers are
  intentional; don't "fix" them down.
- Never put secrets in the repo; /srv/memory/secrets/.env is the only home.
- If RAM < 8 GB: skip ollama + mail_ml entirely (their timers just won't be
  installed); everything else works, cloud-optional.

Start with Phase 0: confirm you can read the repo, list what I need to have
ready, and show me the plan for Phase 1.
```

---

## After install

- `/setup` — connect sources (all in the browser).
- `/sources` — ongoing health; `/health` — every worker unit.
- Backups: see `runbook.md` (restore-drill included). Set them up in week 1,
  not "later".
- Mark private people as **Sensitive** on their contact page — their message
  processing then never leaves your server.

## Staying up to date

Your instance tells you when a newer ADAM is published. The `/today` dashboard
shows an **"ADAM &lt;version&gt; is available"** banner with the release notes; dismissing
it hides that version only, so the next release surfaces again.

- Your installed version lives in `VERSION` at the repo root.
- The check compares it against the latest **GitHub Release** on the upstream
  repo (`ADAM_UPDATE_REPO`, default `dakakoe/adam-personal-os`), cached ~6h.
  It's read-only and fail-soft: offline or rate-limited simply shows no banner.
- Prefer email? On the upstream repo click **Watch → Custom → Releases**.

To upgrade, pull the new snapshot and redeploy (run any new migrations first —
`migrations/` is applied with dbmate).
