"""The consent gate trusts an identity only through the proxy, and the static
token keeps working as a permanent access token without touching the DB."""
from __future__ import annotations

import asyncio

from server import oauth


def test_identity_needs_the_proxy_secret() -> None:
    hdrs = {"x-proxy-secret": "s3cret", "remote-user": "owner"}
    assert oauth.proxied_identity(hdrs, "s3cret") == "owner"
    assert oauth.proxied_identity(hdrs, "other") is None
    assert oauth.proxied_identity({"remote-user": "owner"}, "s3cret") is None


def test_no_secret_configured_trusts_nobody() -> None:
    assert oauth.proxied_identity({"x-proxy-secret": "", "remote-user": "owner"}, None) is None


def test_blank_user_is_no_identity() -> None:
    assert oauth.proxied_identity({"x-proxy-secret": "s", "remote-user": "  "}, "s") is None


class _NoPool:
    """A pool getter that must never be reached."""
    async def __call__(self):
        raise AssertionError("static token must not hit the database")


class _EmptyPool:
    class _Conn:
        async def fetchrow(self, *a):
            return None

    class _Acq:
        async def __aenter__(self):
            return _EmptyPool._Conn()

        async def __aexit__(self, *a):
            return False

    def acquire(self):
        return _EmptyPool._Acq()


async def _empty_pool():
    return _EmptyPool()


def test_static_token_is_a_permanent_access_token() -> None:
    p = oauth.AdamOAuthProvider(_NoPool(), "tok-123", "https://x.example", None)
    tok = asyncio.run(p.load_access_token("tok-123"))
    assert tok is not None
    assert tok.client_id == oauth.STATIC_CLIENT
    assert tok.scopes == [oauth.SCOPE]
    assert tok.expires_at is None


def test_other_tokens_go_to_the_database() -> None:
    p = oauth.AdamOAuthProvider(_empty_pool, "tok-123", "https://x.example", None)
    assert asyncio.run(p.load_access_token("tok-124")) is None


def test_no_static_token_means_no_static_path() -> None:
    p = oauth.AdamOAuthProvider(_empty_pool, None, "https://x.example", None)
    assert asyncio.run(p.load_access_token("")) is None


def test_consent_mode_follows_the_proxy_secret() -> None:
    assert oauth.AdamOAuthProvider(_NoPool(), "t", "https://x", "s").consent_via_proxy
    assert not oauth.AdamOAuthProvider(_NoPool(), "t", "https://x", None).consent_via_proxy
