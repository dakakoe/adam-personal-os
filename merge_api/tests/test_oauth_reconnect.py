"""Tests for the pure logic of the web Google re-consent flow. The token
exchange / profile lookup are network calls (not covered here); this pins down
the parts that silently break the flow if wrong: client-secret parsing, scope
mapping, the consent-URL params (a wrong redirect_uri or a missing
access_type/prompt means Google never returns a refresh_token), and the
configured/not-configured gate."""

from __future__ import annotations

import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from merge_api import oauth_reconnect as orc


def _write_secrets(tmp_path, kind="web", **over) -> str:
    inner = {"client_id": "cid.apps.googleusercontent.com", "client_secret": "sec"}
    inner.update(over)
    p = tmp_path / "client.json"
    p.write_text(json.dumps({kind: inner}))
    return str(p)


def _cfg(secrets_path, redirect="https://m.example/api/sources/oauth/callback",
         scopes="gmail,contacts,calendar"):
    return SimpleNamespace(
        gcal_client_secrets=secrets_path,
        oauth_redirect_uri=redirect,
        oauth_scopes=scopes,
    )


# --- _read_web_secrets ----------------------------------------------------

def test_read_web_secrets_web_key(tmp_path):
    s = orc._read_web_secrets(_write_secrets(tmp_path, "web"))
    assert s == {"client_id": "cid.apps.googleusercontent.com",
                 "client_secret": "sec",
                 "token_uri": "https://oauth2.googleapis.com/token"}


def test_read_web_secrets_installed_key_also_accepted(tmp_path):
    # Desktop ("installed") files still parse, so a half-migrated setup degrades
    # to a clear Google-side error rather than crashing here.
    assert orc._read_web_secrets(_write_secrets(tmp_path, "installed")) is not None


def test_read_web_secrets_missing_client_id_is_none(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"web": {"client_secret": "sec"}}))
    assert orc._read_web_secrets(str(p)) is None


def test_read_web_secrets_unreadable_path_is_none():
    assert orc._read_web_secrets("/no/such/file.json") is None


# --- _scope_urls ----------------------------------------------------------

def test_scope_urls_maps_known_names():
    assert orc._scope_urls(["gmail", "calendar"]) == [
        orc.SCOPES_BY_NAME["gmail"], orc.SCOPES_BY_NAME["calendar"]]


def test_scope_urls_drops_unknown_names():
    assert orc._scope_urls(["gmail", "bogus", "calendar"]) == [
        orc.SCOPES_BY_NAME["gmail"], orc.SCOPES_BY_NAME["calendar"]]


def test_scope_urls_empty_falls_back_to_gmail():
    assert orc._scope_urls([]) == [orc.SCOPES_BY_NAME["gmail"]]


# --- scopes_for_account ---------------------------------------------------

def _scope_cfg(scopes="gmail,contacts,calendar", work=None, send=None):
    return SimpleNamespace(oauth_scopes=scopes,
                           work_calendar_account=work, email_send_account=send)


def test_scopes_for_account_personal_stays_base():
    cfg = _scope_cfg(work="work@x.com", send="work@x.com")
    assert orc.scopes_for_account(cfg, "me@gmail.com") == ["gmail", "contacts", "calendar"]


def test_scopes_for_account_work_gains_write_scopes():
    cfg = _scope_cfg(work="work@x.com", send="work@x.com")
    names = orc.scopes_for_account(cfg, "work@x.com")
    assert "calendar-write" in names and "gmail-send" in names


def test_scopes_for_account_match_is_case_insensitive():
    cfg = _scope_cfg(work="Work@X.com")
    assert "calendar-write" in orc.scopes_for_account(cfg, "work@x.COM")


def test_scopes_for_account_none_email_is_base():
    cfg = _scope_cfg(work="work@x.com", send="work@x.com")
    assert orc.scopes_for_account(cfg, None) == ["gmail", "contacts", "calendar"]


def test_scopes_for_account_no_duplicate_when_already_present():
    cfg = _scope_cfg(scopes="gmail,calendar-write", work="work@x.com")
    assert orc.scopes_for_account(cfg, "work@x.com").count("calendar-write") == 1


# --- build_auth_url -------------------------------------------------------

def test_build_auth_url_has_required_params(tmp_path):
    cfg = _cfg(_write_secrets(tmp_path))
    url = orc.build_auth_url(cfg, state="nonce123")
    q = parse_qs(urlparse(url).query)
    assert q["client_id"] == ["cid.apps.googleusercontent.com"]
    assert q["redirect_uri"] == [cfg.oauth_redirect_uri]
    assert q["response_type"] == ["code"]
    assert q["access_type"] == ["offline"]   # without these two Google may
    assert q["prompt"] == ["consent"]        # not return a refresh_token
    assert q["state"] == ["nonce123"]
    assert "gmail.readonly" in q["scope"][0]


def test_build_auth_url_uses_explicit_scopes_and_login_hint(tmp_path):
    cfg = _cfg(_write_secrets(tmp_path))
    url = orc.build_auth_url(cfg, "st", scope_names=["gmail", "calendar-write"],
                             login_hint="work@x.com")
    q = parse_qs(urlparse(url).query)
    assert "calendar.events" in q["scope"][0]
    assert q["login_hint"] == ["work@x.com"]


def test_build_auth_url_omits_login_hint_when_none(tmp_path):
    url = orc.build_auth_url(_cfg(_write_secrets(tmp_path)), "st")
    assert "login_hint" not in parse_qs(urlparse(url).query)


def test_build_auth_url_raises_when_secrets_unreadable():
    with pytest.raises(RuntimeError):
        orc.build_auth_url(_cfg("/no/such/file.json"), state="x")


# --- reconnect_configured -------------------------------------------------

def test_reconnect_configured_true_when_set(tmp_path):
    assert orc.reconnect_configured(_cfg(_write_secrets(tmp_path))) is True


def test_reconnect_configured_false_without_redirect(tmp_path):
    assert orc.reconnect_configured(_cfg(_write_secrets(tmp_path), redirect=None)) is False


def test_reconnect_configured_false_when_secrets_missing():
    assert orc.reconnect_configured(_cfg("/no/such/file.json")) is False


def test_scopes_for_account_none_is_base_only():
    # setup wizard new=1: a brand-new account gets base scopes only — never
    # the work-calendar/send extras that belong to specific known accounts.
    class Cfg:
        oauth_scopes = "gmail,contacts"
        work_calendar_account = "work@x.com"
        email_send_account = "send@x.com"
    names = orc.scopes_for_account(Cfg(), None)
    assert "calendar-write" not in names
    assert "gmail-send" not in names
