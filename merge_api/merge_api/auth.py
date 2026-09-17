"""Bearer-token auth with two on-ramps:
  1. Authorization: Bearer <token> header (programmatic clients)
  2. httpOnly cookie set by POST /api/login (browser; remembers between visits)

Single-user app — one token compared with constant-time equality, no
database lookup. Token comes from MERGE_API_BEARER_TOKEN in env."""

from __future__ import annotations

import hmac
import logging

from fastapi import HTTPException, Request, status

log = logging.getLogger(__name__)


def _extract_bearer(req: Request) -> str | None:
    h = req.headers.get("authorization")
    if h and h.lower().startswith("bearer "):
        return h.split(" ", 1)[1].strip()
    return None


def _extract_cookie(req: Request, cookie_name: str) -> str | None:
    return req.cookies.get(cookie_name)


def role_for_token(cfg, token: str | None) -> str | None:
    """Map a presented token to its role: 'admin' (owner) | 'budget' (member) |
    None (invalid). Constant-time compares; shared by verify() and the
    role-guard middleware."""
    if not token:
        return None
    if hmac.compare_digest(token, cfg.bearer_token):
        return "admin"
    if cfg.budget_token and hmac.compare_digest(token, cfg.budget_token):
        return "budget"
    return None


def presented_token(request: Request) -> str | None:
    cfg = request.app.state.cfg
    return _extract_bearer(request) or _extract_cookie(request, cfg.cookie_name)


def _role_for_proxy(cfg, request: Request) -> str | None:
    """Map a trusted Authelia identity to a role. We ONLY trust the Remote-Groups
    header when the request also carries the shared X-Proxy-Secret — that proves
    it came through Caddy's forward-auth, not a direct hit to :9100 on the tailnet
    where a client could forge headers. Disabled (returns None) when no secret is
    configured, so this is a no-op until Stage 2's Caddy change is live."""
    secret = getattr(cfg, "proxy_secret", None)
    if not secret:
        return None
    presented = request.headers.get("x-proxy-secret")
    if not presented or not hmac.compare_digest(presented, secret):
        return None
    groups = {g.strip().lower() for g in (request.headers.get("remote-groups") or "").split(",")}
    if "admin" in groups:
        return "admin"
    if "budget" in groups:
        return "budget"
    return None


def role_for_request(request: Request) -> str | None:
    """The caller's role from either a trusted Authelia identity (humans, via the
    forward-auth proxy) OR a bearer token / session cookie (machines + legacy
    browser login). None if neither is valid. Shared by verify() and the
    role-guard middleware so both agree on identity."""
    cfg = request.app.state.cfg
    return _role_for_proxy(cfg, request) or role_for_token(cfg, presented_token(request))


def verify(request: Request) -> str:
    """FastAPI dependency. Returns the caller's role ('admin'|'budget'); raises
    401 if neither a trusted Authelia identity nor a valid token/cookie is present."""
    role = role_for_request(request)
    if role is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="missing or bad token")
    return role


def _proxy_email(cfg, request: Request) -> str | None:
    """Authelia Remote-Email, trusted ONLY when the shared X-Proxy-Secret proves
    the request came through Caddy forward-auth (same trust model as the groups
    header in role_for_request)."""
    secret = getattr(cfg, "proxy_secret", None)
    if not secret:
        return None
    presented = request.headers.get("x-proxy-secret")
    if not presented or not hmac.compare_digest(presented, secret):
        return None
    return (request.headers.get("remote-email") or "").strip() or None


async def resolve_viewer(request: Request) -> dict | None:
    """The data-scoping identity for finance reads/writes (sharing PR1).
    None  → full access (an app-owner: the admin role, the bot, workers).
    dict  → a scoped member: {member_id, role:'member', is_owner:False};
            member_id may be None if the budget caller has no fin_member row yet
            (then they see only shared accounts — never another member's private
            ones). Resolution is by Authelia email when available, else the
            seeded budget member ('member') for token/cookie back-compat."""
    from . import queries
    if role_for_request(request) != "budget":
        return None
    cfg = request.app.state.cfg
    pool = request.app.state.pool
    email = _proxy_email(cfg, request)
    m = await queries.get_member_by_email(pool, email) if email else None
    if not m:
        m = await queries.get_member_by_actor(pool, "member")
    return {"member_id": (m["id"] if m else None), "role": "member", "is_owner": False}
