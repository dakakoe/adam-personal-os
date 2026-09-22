# Rspamd — mail spam scoring (Mail auto-triage Phase B2)

Self-hosted [Rspamd](https://rspamd.com) (Apache 2.0) scores ingested Gmail
messages for spam. `merge_api` fetches each message's raw RFC822 (Gmail
`format=raw`) and POSTs it to Rspamd's `/checkv2`; the `{score, action, symbols}`
verdict is cached in `memory.mail_spam` and surfaced in `/mail`.

## Deploy (on the droplet `memory`)

1. Copy the config into place (this dir → droplet):
   ```
   rsync -a deploy/rspamd/local.d/  memory:/srv/memory/rspamd/local.d/
   ```
2. Add the `rspamd` service to `/srv/memory/docker-compose.yml` (see
   `compose-service.yml` here) and bring it up:
   ```
   docker compose --env-file /srv/memory/secrets/.env up -d rspamd
   ```
3. `merge_api` reads `RSPAMD_URL` (default `http://127.0.0.1:11333`) — no env
   change needed since the service publishes `127.0.0.1:11333`.

## Notes
- Baseline rule-based scoring (SPF/DKIM/DMARC/RBLs/heuristics) + Redis-backed
  stats. **No Bayes training yet** — accuracy improves once trained, but the
  rule score is useful immediately.
- The normal worker (`:11333`) serves `/checkv2` with no password; it is bound to
  `127.0.0.1` on the host and only reachable from `merge_api`.
- Redis is the existing `redis` compose service (shared with Twenty/Authelia);
  Rspamd uses its own key prefixes so there's no collision.
