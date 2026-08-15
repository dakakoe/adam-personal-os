# ADAM · Personal OS

A self-hosted **Personal OS**: your mail, chats, contacts, meetings, tasks,
deals, and household budget on your own server — with an AI memory layer that
runs *for* you, not on someone else's cloud. One box, Postgres underneath,
plain web UI on top, local LLM for anything private.

> Internals note: the codebase and server keep the original codename
> `memory` (`/srv/memory`, `memory-*` systemd units, the `memory` database).
> ADAM is the product name; the plumbing is deliberately unrenamed.

## What it does

- **Mail** — full webmail over your Gmail accounts (read, compose/reply with
  WYSIWYG, attachments, two-way archive/star/trash, snooze, undo). Automatic
  triage: Gmail categories → header heuristics → self-hosted Rspamd spam
  (with continuously trained Bayes) → a SetFit classifier
  (newsletter / transactional / personal) that retrains itself weekly and
  accepts your corrections as ground truth.
- **Chats** — live Telegram ingestion (private chats + opt-in groups), voice
  transcription via local Whisper.
- **Contacts** — cross-channel identity resolution into one person graph,
  LLM-written profiles, merge review, per-contact sharing (household) and
  a **Sensitive** flag: marked contacts are only ever processed by the local
  LLM — their messages never leave the box.
- **Meetings** — Granola recap ingestion with action-item extraction.
- **Work** — tasks, routines, projects, opportunities; suggestions scanned
  out of your real conversations; a daily plan and a nightly digest (the
  digest narrative is generated entirely on-box).
- **Budget** — dual-leg finance ledger, bank-statement import, receipt-photo
  capture via the Telegram bot, multi-user household access.
- **AI memory** — pgvector embeddings over messages AND mail, semantic
  search, and `local_ask`: private RAG answered by the on-box model, exposed
  (with the rest of the memory tools) over MCP to any Claude client.
- **Ops** — every worker is a systemd unit with failure → Telegram alerting;
  nightly backups with a scheduled restore drill.

## Architecture (one server)

```
Caddy (TLS) → Authelia (2FA/passkey) → Next.js UI + FastAPI (merge_api)
Postgres 16 + pgvector   ← the single source of truth (raw / canonical / memory schemas)
Redis · Rspamd · Ollama (qwen2.5:3b)
~40 systemd units: fetchers (gmail, telethon, granola, …), workers
(embedder, whisper, profile builder, scanner, digest, mail_ml), timers
```

Stack: Docker Compose for the infra services, plain venv-per-app Python for
workers, dbmate migrations, no proprietary dependencies. An Anthropic API
key is optional — features degrade gracefully, and privacy-critical paths
(digest, sensitive contacts, local_ask) are local-LLM by design.

## Install your own

**[FRIEND_INSTALL.md](./FRIEND_INSTALL.md)** — server sizing, prerequisites,
and a paste-able Claude Code prompt that drives the whole install, ending in
the in-app **Setup wizard** (`/setup`) which connects Google, Telegram,
Granola, and LinkedIn entirely from the browser.

Deep references:
- [`INFRASTRUCTURE.md`](./INFRASTRUCTURE.md) — the original phased
  provisioning guide (historical in places; FRIEND_INSTALL.md is the
  current entry point)
- [`SCHEMA.md`](./SCHEMA.md) — database layout
- [`runbook.md`](./runbook.md) — incident response
- `deploy/*/README.md` — per-feature install notes (ollama, rspamd,
  mail_ml, alerting, …)

## Development

- Branch (`feat/…`, `fix/…`) → PR. Never push `main`.
- Backend tests: `cd merge_api && pytest tests/` (pure-function style, no DB
  fixtures). The UI's type gate is `next build`.
- **`./scripts/sql-smoke.sh` before shipping a query change.** The offline
  suite can't see inside a SQL string, so a syntax error or a stale column
  name reaches production intact. This sends every statement in `queries.py`
  to Postgres with `PREPARE` — full parse and plan, nothing executed — against
  a throwaway structure-only copy of the live schema.
- Deploy = rsync to the server (preserve exec bits!), `dbmate up` **before**
  restarts, `systemctl restart` the touched units.
