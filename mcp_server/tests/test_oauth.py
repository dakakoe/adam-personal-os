"""The consent gate trusts an identity only through the proxy, and the static
token keeps working as a permanent access token without touching the DB."""
from __future__ import annotations

import asyncio
import json

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



class _RecordingPool:
    """Captures the arguments of every execute/fetchval so a test can see
    exactly what would reach asyncpg (which has no JSONB codec here)."""
    def __init__(self):
        self.calls = []

    class _Conn:
        def __init__(self, outer):
            self.outer = outer

        async def execute(self, sql, *args):
            self.outer.calls.append(("execute", args))

        async def fetchval(self, sql, *args):
            self.outer.calls.append(("fetchval", args))
            return None

    class _Acq:
        def __init__(self, outer):
            self.outer = outer

        async def __aenter__(self):
            return _RecordingPool._Conn(self.outer)

        async def __aexit__(self, *a):
            return False

    def acquire(self):
        return _RecordingPool._Acq(self)


def test_json_columns_are_written_as_strings() -> None:
    from mcp.shared.auth import OAuthClientInformationFull
    pool = _RecordingPool()

    async def getter():
        return pool

    p = oauth.AdamOAuthProvider(getter, "tok", "https://x.example", None)
    client = OAuthClientInformationFull(client_id="c1", redirect_uris=["https://claude.ai/api/mcp/auth_callback"],
                                        client_name="Claude", token_endpoint_auth_method="none")
    asyncio.run(p.register_client(client))
    kind, args = pool.calls[-1]
    assert kind == "execute" and args[0] == "c1"
    assert isinstance(args[1], str) and json.loads(args[1])["client_name"] == "Claude"


def test_json_columns_are_read_back_from_strings() -> None:
    assert oauth._loads('{"a": 1}') == {"a": 1}
    assert oauth._loads({"a": 1}) == {"a": 1}
    assert oauth._loads('["adam"]') == ["adam"]



def test_consent_page_renders_with_css_and_escapes_input() -> None:
    page = oauth.render_page("Claude <x>", "req&1", "owner")
    assert "body{margin:0" in page                      # the stylesheet survived .format
    assert "Allow Claude &lt;x&gt; to use ADAM?" in page
    assert 'value="req&amp;1"' in page and "Signed in as owner" in page
    form = oauth.render_page("Claude", "r", None, error='<p class="error">no</p>')
    assert 'name="token"' in form and '<p class="error">no</p>' in form



class _TxPool:
    """A pool whose connection supports transaction(), fetchrow, execute and
    executemany — enough to run the refresh exchange end to end."""
    def __init__(self, row):
        self.row = row
        self.calls = []

    class _Tx:
        async def __aenter__(self): return None
        async def __aexit__(self, *a): return False

    class _Conn:
        def __init__(self, outer): self.outer = outer
        def transaction(self): return _TxPool._Tx()
        async def fetchrow(self, sql, *args):
            self.outer.calls.append(("fetchrow", args)); return self.outer.row
        async def execute(self, sql, *args):
            self.outer.calls.append(("execute", args))
        async def executemany(self, sql, rows):
            self.outer.calls.append(("executemany", rows))

    class _Acq:
        def __init__(self, outer): self.outer = outer
        async def __aenter__(self): return _TxPool._Conn(self.outer)
        async def __aexit__(self, *a): return False

    def acquire(self): return _TxPool._Acq(self)


def test_refresh_exchange_rotates_and_keeps_the_resource() -> None:
    from datetime import datetime, timedelta, timezone
    from mcp.server.auth.provider import RefreshToken
    from mcp.shared.auth import OAuthClientInformationFull
    row = {"client_id": "c1", "scopes": '["adam"]', "resource": "https://x.example/mcp",
           "expires_at": datetime.now(timezone.utc) + timedelta(days=1)}
    pool = _TxPool(row)

    async def getter(): return pool

    p = oauth.AdamOAuthProvider(getter, None, "https://x.example", None)
    client = OAuthClientInformationFull(client_id="c1", redirect_uris=["https://claude.ai/cb"])
    rt = asyncio.run(p.load_refresh_token(client, "refresh-1"))
    assert rt is not None and rt.scopes == ["adam"]
    out = asyncio.run(p.exchange_refresh_token(client, rt, []))
    assert out.access_token and out.refresh_token and out.refresh_token != "refresh-1"
    kinds = [k for k, _ in pool.calls]
    assert kinds[-3:] == ["fetchrow", "execute", "executemany"]       # read resource, delete old, issue pair
    issued = pool.calls[-1][1]
    assert all(r[4] == "https://x.example/mcp" for r in issued)      # resource carried over
    assert all(isinstance(r[3], str) for r in issued)                 # scopes as json text
