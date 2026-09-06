from __future__ import annotations

import hmac
import logging
import os
import sys

from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse

from . import config, db, embed, tools

log = logging.getLogger(__name__)


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


class _BearerAuth:
    """ASGI wrapper: reject any HTTP request without `Authorization: Bearer
    <token>`. This is the server's OWN gate — Caddy already requires+forwards
    the same token on the public path, so the through-proxy path is unaffected;
    what this closes is the tailnet-DIRECT path, previously unauthenticated.
    Constant-time compare; everything non-HTTP (lifespan) passes through."""

    def __init__(self, app, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        raw = headers.get(b"authorization", b"").decode("latin-1")
        presented = raw[7:].strip() if raw.lower().startswith("bearer ") else ""
        if not (presented and hmac.compare_digest(presented, self.token)):
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"text/plain")]})
            await send({"type": "http.response.body", "body": b"unauthorized"})
            return
        await self.app(scope, receive, send)


def main() -> int:
    os.umask(0o077)
    _setup_logging()

    cfg = config.load()
    db.configure(cfg)

    mcp = FastMCP(
        "memory",
        host=cfg.host,
        port=cfg.port,
        # streamable-http binds /mcp endpoint by default.
    )
    tools.register(mcp)

    if not cfg.bearer_token:
        log.warning("MCP_BEARER_TOKEN unset — the server is UNAUTHENTICATED. "
                    "Anything that can reach %s:%d has full tool access. Set "
                    "MCP_BEARER_TOKEN to require a token.", cfg.host, cfg.port)
        log.info("MCP server listening on %s:%d (no auth)", cfg.host, cfg.port)
        mcp.run(transport="streamable-http")
        return 0

    # Wrap the streamable-http ASGI app in the bearer gate and serve it
    # ourselves (mcp.run would bypass the wrapper). The app carries FastMCP's
    # lifespan (session manager), which uvicorn honors.
    import uvicorn
    inner = mcp.streamable_http_app()

    async def _embed_route(request):
        """Encode a query with the SAME e5-base model the semantic-search tools
        use, so other services (merge_api's /prospects ICP search) can rank
        against memory.profile.embedding without loading their own copy of torch.
        Bearer-gated by the wrapper below, same as every other HTTP path."""
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        q = (body.get("query") or "").strip()
        if not q:
            return JSONResponse({"error": "empty query"}, status_code=400)
        return JSONResponse({"vector": await embed.encode_query(q)})

    inner.add_route("/internal/embed", _embed_route, methods=["POST"])
    app = _BearerAuth(inner, cfg.bearer_token)
    log.info("MCP server listening on %s:%d (bearer auth on)", cfg.host, cfg.port)
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
