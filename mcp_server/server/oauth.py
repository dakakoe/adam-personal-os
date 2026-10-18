"""OAuth 2.1 for the connector, backed by Postgres (memory.oauth_*).

Claude registers itself (dynamic client registration), sends the owner to
/authorize, and the SDK hands the request to `authorize()`. We park it and
send the browser to /oauth/login. Caddy puts that path behind Authelia, so by
the time a request reaches us the owner has done password + 2FA; we trust the
identity headers only when the hop carries the proxy secret — the contract
merge_api already uses. The page is then a one-click consent. With no proxy
secret configured (no Authelia in front) it falls back to asking for the
static MCP token, the way the curator connector does.

The static MCP_BEARER_TOKEN stays valid as a permanent access token, so a
client that already sends it (Claude Code) needs no change.

Access tokens live an hour, refresh tokens thirty days and rotate on use;
both are stored as SHA-256 hashes.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import html
import json
import secrets
import time
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone

from mcp.server.auth.provider import (
    AccessToken, AuthorizationCode, AuthorizationParams, RefreshToken,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

SCOPE = "adam"
STATIC_CLIENT = "static-token"

ACCESS_TTL = 8 * 3600      # a workday; refresh tokens rotate for 30 days
REFRESH_TTL = 30 * 24 * 3600
CODE_TTL = 300
PENDING_TTL = 900


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# The pool has no JSONB codec (same as the rest of this server), so JSON
# columns are written as strings and may come back as strings.
def _dumps(v) -> str:
    return json.dumps(v, default=str)


def _loads(v):
    return json.loads(v) if isinstance(v, str) else v


def proxied_identity(headers: Mapping[str, str], secret: str | None) -> str | None:
    """The Authelia user on this request, or None. Only a hop that presents
    the proxy secret is believed — a direct hit on :9000 can write any header
    it likes. No secret configured → nothing is ever trusted."""
    if not secret:
        return None
    presented = headers.get("x-proxy-secret") or ""
    if not presented or not hmac.compare_digest(presented, secret):
        return None
    return (headers.get("remote-user") or "").strip() or None


class AdamOAuthProvider:
    """Implements mcp.server.auth.provider.OAuthAuthorizationServerProvider."""

    def __init__(self, pool_getter, static_token: str | None, public_url: str,
                 proxy_secret: str | None):
        self._pool = pool_getter
        self._static = static_token
        self._proxy_secret = proxy_secret
        self.public_url = public_url.rstrip("/")

    @property
    def consent_via_proxy(self) -> bool:
        return bool(self._proxy_secret)

    def identity(self, request: Request) -> str | None:
        return proxied_identity(request.headers, self._proxy_secret)

    def token_matches(self, presented: str) -> bool:
        return bool(self._static) and hmac.compare_digest(presented.strip(), self._static)

    # --- clients ---------------------------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        pool = await self._pool()
        async with pool.acquire() as conn:
            info = await conn.fetchval(
                "SELECT info FROM memory.oauth_client WHERE client_id = $1", client_id)
        return OAuthClientInformationFull.model_validate(_loads(info)) if info else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        pool = await self._pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO memory.oauth_client (client_id, info) VALUES ($1, $2)
                   ON CONFLICT (client_id) DO UPDATE SET info = excluded.info""",
                client_info.client_id, _dumps(client_info.model_dump(mode="json")))

    # --- authorization ---------------------------------------------------

    async def authorize(self, client: OAuthClientInformationFull,
                        params: AuthorizationParams) -> str:
        request_id = secrets.token_urlsafe(24)
        pool = await self._pool()
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM memory.oauth_pending WHERE expires_at < now()")
            await conn.execute(
                """INSERT INTO memory.oauth_pending (id, client_id, params, expires_at)
                   VALUES ($1, $2, $3, $4)""",
                request_id, client.client_id, _dumps(params.model_dump(mode="json")),
                _now() + timedelta(seconds=PENDING_TTL))
        return f"{self.public_url}/oauth/login?request={request_id}"

    async def pending_client_name(self, request_id: str) -> str | None:
        pool = await self._pool()
        async with pool.acquire() as conn:
            info = await conn.fetchval(
                """SELECT c.info FROM memory.oauth_pending p
                   JOIN memory.oauth_client c USING (client_id)
                   WHERE p.id = $1 AND p.expires_at > now()""", request_id)
        if not info:
            return None
        info = _loads(info)
        return info.get("client_name") or info.get("client_id")

    async def complete_login(self, request_id: str) -> str | None:
        """The owner said yes. Returns the client's redirect URL carrying a
        fresh code, or None when the request expired. Identity is the
        caller's business — see the routes below."""
        pool = await self._pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "DELETE FROM memory.oauth_pending WHERE id = $1 AND expires_at > now() RETURNING *",
                request_id)
            if not row:
                return None
            params = AuthorizationParams.model_validate(_loads(row["params"]))
            code = AuthorizationCode(
                code=secrets.token_urlsafe(32),
                scopes=params.scopes or [SCOPE],
                expires_at=time.time() + CODE_TTL,
                client_id=row["client_id"],
                code_challenge=params.code_challenge,
                redirect_uri=params.redirect_uri,
                redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
                resource=params.resource,
            )
            await conn.execute(
                """INSERT INTO memory.oauth_code (code, client_id, data, expires_at)
                   VALUES ($1, $2, $3, $4)""",
                code.code, code.client_id, _dumps(code.model_dump(mode="json")),
                _now() + timedelta(seconds=CODE_TTL))
        return construct_redirect_uri(str(params.redirect_uri), code=code.code, state=params.state)

    async def load_authorization_code(self, client: OAuthClientInformationFull,
                                      authorization_code: str) -> AuthorizationCode | None:
        pool = await self._pool()
        async with pool.acquire() as conn:
            data = await conn.fetchval(
                """SELECT data FROM memory.oauth_code
                   WHERE code = $1 AND client_id = $2 AND expires_at > now()""",
                authorization_code, client.client_id)
        return AuthorizationCode.model_validate(_loads(data)) if data else None

    async def exchange_authorization_code(self, client: OAuthClientInformationFull,
                                          authorization_code: AuthorizationCode) -> OAuthToken:
        pool = await self._pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                gone = await conn.fetchval(
                    "DELETE FROM memory.oauth_code WHERE code = $1 RETURNING 1",
                    authorization_code.code)
                if not gone:          # a code works exactly once
                    raise ValueError("authorization code already used")
                return await self._issue(conn, client.client_id, authorization_code.scopes,
                                         authorization_code.resource)

    # --- tokens ----------------------------------------------------------

    async def _issue(self, conn, client_id: str, scopes: list[str],
                     resource: str | None) -> OAuthToken:
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        await conn.executemany(
            """INSERT INTO memory.oauth_token (token_hash, kind, client_id, scopes, resource, expires_at)
               VALUES ($1, $2, $3, $4, $5, $6)""",
            [(_hash(access), "access", client_id, _dumps(scopes), resource,
              _now() + timedelta(seconds=ACCESS_TTL)),
             (_hash(refresh), "refresh", client_id, _dumps(scopes), resource,
              _now() + timedelta(seconds=REFRESH_TTL))])
        return OAuthToken(access_token=access, token_type="Bearer", expires_in=ACCESS_TTL,
                          refresh_token=refresh, scope=" ".join(scopes))

    async def _load(self, token: str, kind: str, client_id: str | None = None):
        pool = await self._pool()
        async with pool.acquire() as conn:
            return await conn.fetchrow(
                """SELECT * FROM memory.oauth_token WHERE token_hash = $1 AND kind = $2
                     AND (expires_at IS NULL OR expires_at > now())
                     AND ($3::text IS NULL OR client_id = $3)""",
                _hash(token), kind, client_id)

    async def load_refresh_token(self, client: OAuthClientInformationFull,
                                 refresh_token: str) -> RefreshToken | None:
        row = await self._load(refresh_token, "refresh", client.client_id)
        if not row:
            return None
        # SDK 1.x RefreshToken has no `resource`; the row keeps it for the exchange.
        return RefreshToken(token=refresh_token, client_id=row["client_id"],
                            scopes=list(_loads(row["scopes"])),
                            expires_at=int(row["expires_at"].timestamp()))

    async def exchange_refresh_token(self, client: OAuthClientInformationFull,
                                     refresh_token: RefreshToken, scopes: list[str]) -> OAuthToken:
        pool = await self._pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                # The resource the token was issued for lives on the row (the
                # SDK's RefreshToken model does not carry it in 1.x).
                row = await conn.fetchrow(
                    "SELECT resource FROM memory.oauth_token WHERE token_hash = $1",
                    _hash(refresh_token.token))
                resource = row["resource"] if row else None
                # Rotation: the old refresh token dies the moment it is used.
                await conn.execute("DELETE FROM memory.oauth_token WHERE token_hash = $1",
                                   _hash(refresh_token.token))
                return await self._issue(conn, client.client_id,
                                         scopes or refresh_token.scopes, resource)

    async def load_access_token(self, token: str) -> AccessToken | None:
        if self.token_matches(token):
            # The pre-OAuth static token: permanent, full scope, no DB row.
            return AccessToken(token=token, client_id=STATIC_CLIENT, scopes=[SCOPE],
                               expires_at=None)
        row = await self._load(token, "access")
        if not row:
            return None
        return AccessToken(token=token, client_id=row["client_id"], scopes=list(_loads(row["scopes"])),
                           expires_at=int(row["expires_at"].timestamp()),
                           resource=row["resource"])

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        if token.client_id == STATIC_CLIENT:
            return      # not ours to revoke; it lives in the environment
        pool = await self._pool()
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM memory.oauth_token WHERE token_hash = $1",
                               _hash(token.token))


# --- the consent page ----------------------------------------------------

_STYLE = """
 body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0f1115;color:#e8eaf0;
      font-family:ui-sans-serif,-apple-system,"Segoe UI",sans-serif;padding:16px}
 .card{max-width:400px;width:100%;display:grid;gap:12px}
 h1{font-size:18px;margin:0} p{color:#8b93a7;font-size:14px;margin:0;line-height:1.5}
 ul{color:#8b93a7;font-size:14px;margin:0;padding-left:18px;line-height:1.6}
 input,button{font:inherit;font-size:14px;padding:9px 11px;border-radius:6px;border:1px solid #272b35;
      background:#171a21;color:#e8eaf0}
 button{background:#5b8def;border-color:#5b8def;color:#0f1115;font-weight:600;cursor:pointer}
 .who{font-size:12px;color:#5f677a} .error{color:#ff6b6b}
"""

_HEAD = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
         '<meta name="viewport" content="width=device-width, initial-scale=1">'
         f'<title>Connect to ADAM</title><style>{_STYLE}</style></head><body>')

_WHAT = """<ul>
<li>read your contacts, messages and mail — except contacts marked sensitive</li>
<li>see and change tasks, follow-ups and deals</li>
<li>write message drafts; sending still needs your yes, each time</li>
<li>send you Telegram notes</li>
</ul>"""

# Only these bodies go through .format(); the stylesheet above has CSS braces
# and is prepended afterwards.
_CONSENT_BODY = """<form class="card" method="post" action="/oauth/login">
<h1>Allow {client} to use ADAM?</h1>
<p>It will be able to:</p>""" + _WHAT + """
<input type="hidden" name="request" value="{request}">
<button type="submit">Allow</button>
<p class="who">Signed in as {user}</p>
</form></body></html>"""

_TOKEN_BODY = """<form class="card" method="post" action="/oauth/login">
<h1>Connect {client} to ADAM</h1>
<p>Enter the MCP token to allow it to:</p>""" + _WHAT + """
{error}
<input type="hidden" name="request" value="{request}">
<input type="password" name="token" placeholder="MCP token" autocomplete="current-password" required autofocus>
<button type="submit">Allow</button>
</form></body></html>"""


def render_page(client: str, request_id: str, user: str | None, error: str = "") -> str:
    """The consent page for a signed-in owner, or the token form without one.
    Everything user-controlled is escaped; `error` is our own markup."""
    if user:
        body = _CONSENT_BODY.format(client=html.escape(client), request=html.escape(request_id),
                                    user=html.escape(user))
    else:
        body = _TOKEN_BODY.format(client=html.escape(client), request=html.escape(request_id),
                                  error=error)
    return _HEAD + body


_EXPIRED = HTMLResponse("<p>This sign-in link has expired. Start again from Claude.</p>",
                        status_code=400)


def register_consent_routes(server, provider: AdamOAuthProvider) -> None:
    def page(client: str, request_id: str, user: str | None, error: str = "") -> HTMLResponse:
        return HTMLResponse(render_page(client, request_id, user, error), status_code=401 if error else 200)

    @server.custom_route("/oauth/login", methods=["GET"], include_in_schema=False)
    async def login_page(request: Request) -> Response:
        request_id = request.query_params.get("request", "")
        client = await provider.pending_client_name(request_id)
        if not client:
            return _EXPIRED
        user = provider.identity(request)
        if provider.consent_via_proxy and not user:
            # Authelia is supposed to be in front of this path; a request that
            # arrived without its identity did not come through it.
            return HTMLResponse("<p>Sign in through the portal first.</p>", status_code=403)
        return page(client, request_id, user)

    @server.custom_route("/oauth/login", methods=["POST"], include_in_schema=False)
    async def login_submit(request: Request) -> Response:
        form = await request.form()
        request_id = str(form.get("request", ""))
        user = provider.identity(request)
        if provider.consent_via_proxy:
            allowed = bool(user)
        else:
            allowed = provider.token_matches(str(form.get("token", "")))
        if allowed:
            redirect = await provider.complete_login(request_id)
            return RedirectResponse(redirect, status_code=302) if redirect else _EXPIRED
        if provider.consent_via_proxy:
            return HTMLResponse("<p>Sign in through the portal first.</p>", status_code=403)
        await asyncio.sleep(1.0)   # a wrong token costs the guesser a second
        client = await provider.pending_client_name(request_id)
        if not client:
            return _EXPIRED
        return page(client, request_id, None, error='<p class="error">That token is not right.</p>')
