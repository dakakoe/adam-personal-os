"""Setup wizard — the pure helpers behind the /api/setup routes: the telethon
marker-line protocol, the linkedin result line, and upload validation."""
from __future__ import annotations

from merge_api.setup_flow import (
    SECRET_SOURCES,
    parse_auth_output,
    parse_linkedin_output,
    valid_upload_name,
)


def test_auth_code_sent() -> None:
    out = "some log noise\nsend_code_result phone_code_hash=abc123XYZ\n"
    r = parse_auth_output(out)
    assert r.status == "code_sent"
    assert r.phone_code_hash == "abc123XYZ"


def test_auth_already_authorized() -> None:
    assert parse_auth_output("already_authorized\n").status == "already_authorized"


def test_auth_signed_in_with_user() -> None:
    r = parse_auth_output("signed_in id=42 username=johndoe\n")
    assert r.status == "signed_in"
    assert r.user == "id=42 username=johndoe"


def test_auth_flood_wait_seconds() -> None:
    r = parse_auth_output("flood_wait seconds=300\n")
    assert r.status == "flood_wait"
    assert r.retry_after_s == 300


def test_auth_error_markers() -> None:
    for marker in ("need_2fa", "bad_code", "code_expired", "bad_password", "bad_phone"):
        assert parse_auth_output(f"{marker}\n").status == marker


def test_auth_last_marker_wins() -> None:
    out = "send_code_result phone_code_hash=first\nbad_code\n"
    assert parse_auth_output(out).status == "bad_code"


def test_auth_garbage_and_empty() -> None:
    assert parse_auth_output("").status == "unknown"
    assert parse_auth_output("Traceback (most recent call last): ...").status == "unknown"
    assert parse_auth_output(None).status == "unknown"  # type: ignore[arg-type]


def test_linkedin_result_parsed() -> None:
    out = "log line\nlinkedin_import_result connections=120 messages=0 imported=5\n"
    assert parse_linkedin_output(out) == {"connections": 120, "messages": 0, "imported": 5}


def test_linkedin_result_missing() -> None:
    assert parse_linkedin_output("no marker here") is None
    assert parse_linkedin_output("") is None


def test_secret_allowlist() -> None:
    assert "granola" in SECRET_SOURCES
    assert "gmail" not in SECRET_SOURCES     # OAuth, never a pasted secret


def test_upload_name_validation() -> None:
    assert valid_upload_name("Basic_LinkedInDataExport.zip")
    assert valid_upload_name("Connections.csv")
    assert not valid_upload_name("../../etc/passwd")
    assert not valid_upload_name("foo/bar.zip")
    assert not valid_upload_name("evil.exe")
    assert not valid_upload_name("")
    assert not valid_upload_name(None)
