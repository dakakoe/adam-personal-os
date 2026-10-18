from __future__ import annotations

import hmac
import logging
import os
import sys
from urllib.parse import urlparse

from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse

from . import assistant, config, db, embed, oauth, tools

log = logging.getLogger(__name__)


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


class _BearerGate:
    """ASGI wrapper: reject an HTTP request without `Authorization: Bearer
    <token>`. With `prefixes` it guards only those paths (the internal
    endpoints, once the SDK's own OAuth middleware guards /mcp); without, it
    is the whole server's gate, which closes the tailnet-direct path a
    token-only deployment would otherwise leave open. Constant-time compare;
    everything non-HTTP (lifespan) passes through."""

    def __init__(self, app, token: str, prefixes: tuple[str, ...] = ()) -> None:
        self.app = app
        self.token = token
        self.prefixes = prefixes

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or ""
        if self.prefixes and not path.startswith(self.prefixes):
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


def _oauth_kwargs(cfg: config.Config) -> tuple[dict, oauth.AdamOAuthProvider | None]:
    """FastMCP constructor arguments that turn on OAuth, when the server has
    a public origin. The SDK then serves /authorize, /token, /register,
    /revoke and discovery, and checks a bearer token on every /mcp call
    through provider.load_access_token (which also accepts the static one)."""
    if not cfg.public_url:
        return {}, None
    provider = oauth.AdamOAuthProvider(db.pool, cfg.bearer_token, cfg.public_url, cfg.proxy_secret)
    host = urlparse(cfg.public_url).netloc
    kwargs = dict(
        auth_server_provider=provider,
        auth=AuthSettings(
            issuer_url=cfg.public_url,
            resource_server_url=f"{cfg.public_url}/mcp",
            client_registration_options=ClientRegistrationOptions(
                enabled=True, valid_scopes=[oauth.SCOPE], default_scopes=[oauth.SCOPE]),
            revocation_options=RevocationOptions(enabled=True),
        ),
        # Host/Origin checks on /mcp: the public name through Caddy, loopback
        # for merge_api's embed calls and local tests.
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[host, "127.0.0.1:*", "localhost:*"],
            allowed_origins=[f"https://{host}", "https://claude.ai", "http://localhost:*"],
        ),
    )
    return kwargs, provider


def main() -> int:
    os.umask(0o077)
    _setup_logging()

    cfg = config.load()
    db.configure(cfg)

    extra, provider = _oauth_kwargs(cfg)
    mcp = FastMCP(
        "memory",
        host=cfg.host,
        port=cfg.port,
        website_url=cfg.public_url,
        # streamable-http binds /mcp endpoint by default.
        **extra,
    )
    tools.register(mcp, cfg)
    assistant.register(mcp, cfg)
    if provider is not None:
        oauth.register_consent_routes(mcp, provider)

    if not cfg.bearer_token and provider is None:
        log.warning("MCP_BEARER_TOKEN unset — the server is UNAUTHENTICATED. "
                    "Anything that can reach %s:%d has full tool access. Set "
                    "MCP_BEARER_TOKEN to require a token.", cfg.host, cfg.port)
        log.info("MCP server listening on %s:%d (no auth)", cfg.host, cfg.port)
        mcp.run(transport="streamable-http")
        return 0

    # Serve the ASGI app ourselves (mcp.run would bypass the wrapper). The app
    # carries FastMCP's lifespan (session manager), which uvicorn honors.
    import uvicorn
    inner = mcp.streamable_http_app()

    async def _embed_route(request):
        """Encode a query with the SAME e5-base model the semantic-search tools
        use, so other services (merge_api's /prospects ICP search) can rank
        against memory.profile.embedding without loading their own copy of torch.
        Static-token gated by the wrapper below."""
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        q = (body.get("query") or "").strip()
        if not q:
            return JSONResponse({"error": "empty query"}, status_code=400)
        return JSONResponse({"vector": await embed.encode_query(q)})

    inner.add_route("/internal/embed", _embed_route, methods=["POST"])
    if provider is not None:
        # OAuth on: the SDK guards /mcp; the static token guards /internal/*;
        # the OAuth endpoints and the consent page are public by design
        # (Caddy puts the consent page behind Authelia).
        app = _BearerGate(inner, cfg.bearer_token or "", prefixes=("/internal/",)) \
            if cfg.bearer_token else inner
        log.info("MCP server listening on %s:%d (OAuth at %s, consent %s)", cfg.host, cfg.port,
                 cfg.public_url, "via Authelia" if provider.consent_via_proxy else "by token")
    else:
        app = _BearerGate(inner, cfg.bearer_token)
        log.info("MCP server listening on %s:%d (bearer auth on)", cfg.host, cfg.port)
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="info",
                proxy_headers=True, forwarded_allow_ips="*")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
