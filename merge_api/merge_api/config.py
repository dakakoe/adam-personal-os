from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    db_url: str
    bearer_token: str            # admin (owner) — full access
    budget_token: str | None     # budget-only role (member) — /api/finance/* only
    budget_person_id: str | None  # the budget member's canonical.person — scopes their membership
    cookie_name: str
    # Shared secret Caddy injects as the X-Proxy-Secret header to prove a request
    # arrived via the Authelia forward-auth proxy (not a direct hit to :9100 on
    # the tailnet). Only when it matches do we trust the Remote-Groups identity.
    # Absent → proxy identity disabled, auth falls back to bearer/cookie exactly
    # as before.
    proxy_secret: str | None
    cors_origin: str | None    # for local dev
    host: str
    port: int
    # Where Telegram avatar JPEGs are written by the fetcher
    # (download-avatars). Served by GET /api/persons/{id}/photo when
    # memory.person_photo points at this directory.
    avatars_dir: str
    # Anthropic — powers the Granola recap extraction (Phase 2). Key is
    # shared with profile_builder via the same .env. Optional: if absent,
    # the /api/ingest/granola endpoint 503s instead of crashing startup.
    anthropic_api_key: str | None
    extraction_model: str
    # Bank/financial statement parsing (/api/finance/import/pdf) is accuracy-
    # critical and infrequent — a weaker model drops or mis-signs rows on long,
    # multi-page statements (esp. single-signed-column layouts like Sber), so it
    # runs on a stronger model than the default extraction_model.
    statement_model: str
    # How the AI prompts should NAME you in generated output — meeting titles
    # ("<self_label> <> Counterparty"), draft sign-offs. The prompts refer to you
    # generically as "the user"; without a real name here the model writes that
    # literally. Same convention as PLANNER_SELF_LABEL / SCANNER_SELF_LABEL.
    self_label: str
    # Calendar write (Telegram capture → event). The work account must be
    # re-consented with the calendar.events scope; client secrets are the same
    # Google "Desktop app" credentials the gmail fetcher uses.
    gcal_client_secrets: str
    work_calendar_account: str | None
    # Browser-based Google re-consent for the Sources page. The redirect URI must
    # be registered on the (Web-application) OAuth client behind GMAIL_CLIENT_
    # SECRETS. Absent → the reconnect endpoints 503 and the UI hides the button,
    # so this is inert until deployed. oauth_scopes are comma-separated shortnames
    # (see oauth_reconnect.SCOPES_BY_NAME).
    oauth_redirect_uri: str | None
    oauth_scopes: str
    # Trigram threshold for cross-source suggestion dedup (Granola vs the
    # Telegram scanner). Matches the scanner's SCANNER_DEDUP_SIMILARITY.
    suggestion_dedup_similarity: float
    # Google account outreach emails send FROM (re-consented with gmail.send).
    email_send_account: str | None
    # ZenMoney personal token — powers the budget import (/api/finance/import/
    # zenmoney + the zenmoney_sync worker). Optional: absent → the import 503s.
    zenmoney_token: str | None
    # Crypto transfer-feed ingestion for operational wallets (crypto_sync.py).
    # Alchemy getAssetTransfers (ETH/Base/Arb/Op/Polygon, full archive history) +
    # NodeReal MegaNode (BSC archive RPC) + Helius (Solana). TronGrid is keyless.
    # Absent → that chain's transfer sync is skipped. etherscan_api_key is legacy
    # (superseded by Alchemy; kept for ad-hoc use).
    etherscan_api_key: str | None
    alchemy_api_key: str | None
    nodereal_api_key: str | None
    helius_api_key: str | None
    trongrid_api_key: str | None
    # Self-hosted Rspamd for mail spam scoring (Phase B2). The normal worker's
    # /checkv2 endpoint; absent → the spam scan 503s. Bound to 127.0.0.1 on the host.
    rspamd_url: str | None
    # Rspamd controller worker (/learnspam, /learnham, /stat) for Bayes training.
    rspamd_controller_url: str | None
    # Local Ollama — sensitive-contact drafts route here instead of Anthropic.
    ollama_url: str
    ollama_model: str
    # The MCP server's internal embed endpoint — encodes a query with the same
    # e5-base model that built memory.profile.embedding, so /prospects ICP search
    # can rank against it without loading torch into this API. Bearer = the MCP
    # server's own token. Absent/unreachable → ICP search 503s (reconnect ranking
    # is unaffected).
    mcp_embed_url: str
    mcp_bearer: str | None


def load() -> Config:
    return Config(
        db_url=os.environ.get("MERGE_API_DATABASE_URL") or _build_db_url(),
        bearer_token=_required("MERGE_API_BEARER_TOKEN"),
        budget_token=os.environ.get("MERGE_API_BUDGET_TOKEN") or None,
        budget_person_id=os.environ.get("MERGE_API_BUDGET_PERSON_ID") or None,
        cookie_name=os.environ.get("MERGE_API_COOKIE", "merge_session"),
        proxy_secret=os.environ.get("MERGE_PROXY_SECRET") or None,
        cors_origin=os.environ.get("MERGE_API_CORS_ORIGIN") or None,
        host=os.environ.get("MERGE_API_HOST", "127.0.0.1"),
        port=int(os.environ.get("MERGE_API_PORT", "9100")),
        avatars_dir=os.environ.get("MERGE_API_AVATARS_DIR", "/srv/memory/data/avatars"),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
        extraction_model=os.environ.get("MERGE_API_EXTRACTION_MODEL", "claude-haiku-4-5"),
        statement_model=os.environ.get("MERGE_API_STATEMENT_MODEL", "claude-sonnet-5"),
        self_label=os.environ.get("MERGE_API_SELF_LABEL", "the user"),
        gcal_client_secrets=os.environ.get(
            "GMAIL_CLIENT_SECRETS", "/srv/memory/secrets/gmail-client-secrets.json"
        ),
        work_calendar_account=os.environ.get("BOT_WORK_CALENDAR_ACCOUNT") or None,
        oauth_redirect_uri=os.environ.get("MERGE_OAUTH_REDIRECT_URI") or None,
        oauth_scopes=os.environ.get(
            # gmail-modify (⊃ gmail.readonly) is the base so a browser reconnect
            # also grants the mail client's two-way archive/star push (Phase 3c);
            # gmail-send so re-consenting ANY account makes it send-capable in the
            # compose From-picker (mail round 2 / P2).
            "MERGE_OAUTH_SCOPES", "gmail-modify,gmail-send,contacts,other-contacts,calendar"
        ),
        suggestion_dedup_similarity=float(
            os.environ.get("MERGE_API_SUGGESTION_DEDUP_SIMILARITY", "0.4")
        ),
        email_send_account=os.environ.get("BOT_EMAIL_SEND_ACCOUNT") or None,
        zenmoney_token=os.environ.get("ZENMONEY_TOKEN") or None,
        etherscan_api_key=os.environ.get("ETHERSCAN_API_KEY") or None,
        alchemy_api_key=os.environ.get("ALCHEMY_API_KEY") or None,
        nodereal_api_key=os.environ.get("NODEREAL_API_KEY") or None,
        helius_api_key=os.environ.get("HELIUS_API_KEY") or None,
        trongrid_api_key=os.environ.get("TRONGRID_API_KEY") or None,
        rspamd_url=os.environ.get("RSPAMD_URL", "http://127.0.0.1:11333"),
        rspamd_controller_url=os.environ.get("RSPAMD_CONTROLLER_URL", "http://127.0.0.1:11334"),
        ollama_url=os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434"),
        mcp_embed_url=os.environ.get("MCP_EMBED_URL", "http://127.0.0.1:9000/internal/embed"),
        mcp_bearer=os.environ.get("MCP_BEARER_TOKEN") or None,
        ollama_model=os.environ.get("MERGE_API_OLLAMA_MODEL") or os.environ.get("OLLAMA_MODEL") or "qwen2.5:3b",
    )


def _required(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise SystemExit(f"missing required env: {name}")
    return val


def _build_db_url() -> str:
    user = _required("POSTGRES_USER")
    pw = _required("POSTGRES_PASSWORD")
    db = _required("POSTGRES_DB")
    host = os.environ.get("POSTGRES_HOST", "127.0.0.1")
    port = os.environ.get("POSTGRES_PORT", "5432")
    return f"postgres://{user}:{pw}@{host}:{port}/{db}"
