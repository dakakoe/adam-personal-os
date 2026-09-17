"""Tests for gmail_fetch credential construction (pure logic — no network).
Pins the contract behind the one-refresh-per-account batch pattern: creds are
built unrefreshed (token=None, not valid), so anything that skips
account_credentials() pays an OAuth round-trip per API call."""

from __future__ import annotations

import json

from merge_api import gmail_fetch


def _write_secrets(tmp_path, kind="installed", **over) -> str:
    inner = {"client_id": "cid.apps.googleusercontent.com", "client_secret": "sec"}
    inner.update(over)
    p = tmp_path / "client.json"
    p.write_text(json.dumps({kind: inner}))
    return str(p)


def test_build_credentials_installed_key(tmp_path):
    creds = gmail_fetch._build_credentials(_write_secrets(tmp_path), "rtok")
    assert creds.refresh_token == "rtok"
    assert creds.client_id == "cid.apps.googleusercontent.com"
    assert creds.client_secret == "sec"
    assert creds.scopes == [gmail_fetch._READONLY]
    assert creds.token is None
    assert creds.valid is False  # unrefreshed until account_credentials()


def test_build_credentials_web_key_and_default_token_uri(tmp_path):
    creds = gmail_fetch._build_credentials(_write_secrets(tmp_path, "web"), "rtok")
    assert creds.token_uri == "https://oauth2.googleapis.com/token"


def test_build_credentials_explicit_scope(tmp_path):
    creds = gmail_fetch._build_credentials(
        _write_secrets(tmp_path), "rtok", gmail_fetch._MODIFY)
    assert creds.scopes == [gmail_fetch._MODIFY]
