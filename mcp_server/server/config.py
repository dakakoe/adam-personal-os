from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    db_url: str
    host: str
    port: int
    # Local LLM for the private local_ask tool (Personal OS Phase 5).
    ollama_url: str
    ollama_model: str
    # Static token. With OAuth on it stays valid as a permanent access token
    # (Claude Code's config keeps working) and gates /internal/*; without
    # OAuth it is the only gate. None → auth disabled (loud warning at
    # startup), which leaves the whole contact graph open to the tailnet.
    bearer_token: str | None
    # The server's public origin, e.g. https://memory.example.com. Set →
    # OAuth endpoints are served and Claude Cowork / claude.ai can connect.
    # Unset → token-only server, as before.
    public_url: str | None
    # Shared secret Caddy adds after Authelia forward-auth. The consent page
    # trusts the Remote-User header only when it is present — the same
    # contract merge_api uses. Unset → the page asks for the static token.
    proxy_secret: str | None
    # merge_api, for the assistant tools that create and change things.
    merge_api_base: str
    merge_api_token: str | None
    # The bot that reaches the owner's phone (same bot as capture + alerts).
    tg_bot_token: str | None
    tg_chat_id: str | None
    # The owner's day boundary for "due today" in the brief. Same default as
    # merge_api's own day math.
    timezone: str
    # Circles whose members find_people must NEVER return (comma-separated
    # circle keys). Empty = no exclusion, which is what a fresh install has.
    # Deliberately not a literal in the code: which circle means "never suggest
    # these people" is personal, and hardcoding it would publish it with the
    # source.
    excluded_circles: tuple[str, ...]
    # Path to the eval harness's need -> circles map (JSON). Unset = the sample
    # in eval/find_people_eval.py; a real need list says what you are working on.
    eval_needs_path: str | None


def load() -> Config:
    return Config(
        db_url=os.environ.get("MCP_DATABASE_URL") or _build_db_url(),
        # Binds all interfaces because Caddy (in a container) reaches us over
        # the docker-bridge gateway, not loopback — binding 127.0.0.1 would cut
        # the proxy off. Public :9000 is blocked by UFW (only 80/443 open); the
        # app authenticates every request itself.
        host=os.environ.get("MCP_HOST", "0.0.0.0"),
        port=int(os.environ.get("MCP_PORT", "9000")),
        ollama_url=os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434"),
        ollama_model=os.environ.get("MCP_OLLAMA_MODEL") or os.environ.get("OLLAMA_MODEL") or "qwen2.5:3b",
        bearer_token=os.environ.get("MCP_BEARER_TOKEN") or None,
        public_url=_public_url(),
        proxy_secret=os.environ.get("MCP_PROXY_SECRET") or os.environ.get("MERGE_PROXY_SECRET") or None,
        merge_api_base=os.environ.get("MCP_MERGE_API_BASE", "http://127.0.0.1:9100").rstrip("/"),
        merge_api_token=os.environ.get("MERGE_API_BEARER_TOKEN") or None,
        tg_bot_token=os.environ.get("ALERT_TG_BOT_TOKEN") or None,
        tg_chat_id=os.environ.get("ALERT_TG_CHAT_ID") or None,
        timezone=os.environ.get("MCP_TIMEZONE") or os.environ.get("TZ") or "Asia/Bangkok",
        excluded_circles=_csv_env("FIND_PEOPLE_EXCLUDED_CIRCLES"),
        eval_needs_path=os.environ.get("FIND_PEOPLE_EVAL_NEEDS") or None,
    )


def _public_url() -> str | None:
    """MCP_PUBLIC_URL, or https:// + the Caddy domain the same env already
    names for this server. Empty → no OAuth."""
    url = os.environ.get("MCP_PUBLIC_URL")
    if not url:
        domain = os.environ.get("CADDY_DOMAIN_MCP")
        url = f"https://{domain}" if domain else None
    return url.rstrip("/") if url else None


def _csv_env(name: str) -> tuple[str, ...]:
    """Comma-separated env value → lower-cased tuple; empty when unset."""
    return tuple(v.strip().lower() for v in os.environ.get(name, "").split(",") if v.strip())


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
