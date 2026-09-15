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
    # Bearer token required on every request (the same token Caddy forwards on
    # the public path). None → auth disabled (loud warning at startup). Closes
    # the tailnet-direct hole: without this the server is reachable unauthed
    # from any node on the tailnet, exposing the whole contact graph.
    bearer_token: str | None
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
        # the proxy off. Public :9000 is blocked by UFW (only 80/443 open) and
        # fronted by Caddy's own bearer gate; the bearer_token below is what
        # protects the tailnet-direct path.
        host=os.environ.get("MCP_HOST", "0.0.0.0"),
        port=int(os.environ.get("MCP_PORT", "9000")),
        ollama_url=os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434"),
        ollama_model=os.environ.get("MCP_OLLAMA_MODEL") or os.environ.get("OLLAMA_MODEL") or "qwen2.5:3b",
        bearer_token=os.environ.get("MCP_BEARER_TOKEN") or None,
        excluded_circles=_csv_env("FIND_PEOPLE_EXCLUDED_CIRCLES"),
        eval_needs_path=os.environ.get("FIND_PEOPLE_EVAL_NEEDS") or None,
    )


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
