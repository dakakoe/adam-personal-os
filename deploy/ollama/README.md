# Ollama — local LLM (Personal OS Phase 5)

Self-hosted LLM on the droplet so private inference never leaves the box.
First customers: the mail classifier's low-confidence tier (`mail_ml refine`) and
ad-hoc private queries. CPU-only (4 vCPU) — fine for short classification calls
(~3–6s each), not interactive chat.

## Service
`ollama/ollama` in `/srv/memory/docker-compose.override.yml`, published on
`127.0.0.1:11434` only. Models live in `/srv/memory/data/ollama`. Tuned for the
shared 8GB box: `mem_limit 3500m`, one model / one parallel request,
`OLLAMA_KEEP_ALIVE=5m` so RAM frees between batches.

## Model — YOUR choice, not bundled

The distribution ships **no model**. You pull one with Ollama and name it once
via `OLLAMA_MODEL` in `/srv/memory/secrets/.env`; every service inherits it
(per-role overrides like `MAIL_ML_OLLAMA_MODEL` exist but are rarely needed).
Because nothing is redistributed, the model's license is entirely between you
and whoever publishes it.

Pick for your server:

| Server RAM | Recommendation | License | Notes |
|---|---|---|---|
| under 8 GB | **skip Ollama** — leave `OLLAMA_MODEL` blank | — | cloud-only; every local-LLM feature degrades gracefully |
| 8 GB | `llama3.2:3b` (~2 GB) | Llama Community (permissive) | the safe friend-install default |
| 8 GB | `qwen2.5:3b` (~2 GB) | Qwen **research** license | stronger multilingual/RU; fine for personal use, check the license before redistributing |
| 16 GB+ | `llama3.1:8b` (~5 GB) | Llama Community | noticeably better drafts/digests; slower |

(This box runs `qwen2.5:3b` for its RU quality — a deliberate personal-use choice.)

## Deploy
```
# service (already in the override on the droplet):
docker compose --env-file /srv/memory/secrets/.env up -d ollama
docker exec memory-ollama-1 ollama pull "$OLLAMA_MODEL"   # the model you chose in .env
curl -s 127.0.0.1:11434/api/tags   # verify it's present
```

## Changing the model later
`ollama pull <new-model>`, set `OLLAMA_MODEL=<new-model>` in `.env`, then
`sudo systemctl restart memory-mcp memory-merge-api` (and the workers pick it
up on their next timer tick). No code or unit-file change.

## Consumers
All read the same `OLLAMA_MODEL` from `.env` (per-role override names shown in
parens for the rare case you want a different model for one job).
- `mail_ml` (`MAIL_ML_OLLAMA_MODEL`): `python -m mail_ml refine` re-classifies
  low-confidence rows; the timer runs a small refine batch after each classify.
- **Sensitivity routing (Ollama expansion)** — a contact marked `sensitive`
  (canonical.person.sensitive, toggle on the contact page) is only ever
  processed locally, FAIL-CLOSED (local failure = skip, never cloud):
  - `interaction_scanner` (`SCANNER_OLLAMA_MODEL`): suggestion extraction
  - `profile_builder` (`PROFILE_OLLAMA_MODEL`): narrative-only profiles
    (structured identifier extraction is deliberately skipped locally)
  - `merge_api` draft outreach (`MERGE_API_OLLAMA_MODEL`): drafts ~30–90s,
    draft row stores the model for provenance
- `digest` (`DIGEST_OLLAMA_MODEL`): the nightly narrative runs WHOLESALE
  locally — its payload is raw message text. Failure → stats-only digest.
- `mcp_server` `local_ask` + `semantic_search_mail` (`MCP_OLLAMA_MODEL`):
  private RAG over interactions AND embedded mail (memory.mail_embedding,
  drained by the embedder in idle gaps).

**Documented cloud exceptions** (no local capacity on this box): payment-slip
vision in capture, bank-statement parsing (finance import), and the daily
planner (low-sensitivity titles only, keeps Haiku narrative quality).
