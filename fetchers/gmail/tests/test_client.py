"""Tests for the OAuth-error classifier that decides when an account needs
re-consent. This is the one piece where a misclassification has a real cost:
treating a transient rate-limit 403 as 'reauth_needed' would wrongly disable a
working account (it drops out of list_active_accounts until manually re-mint)."""

from __future__ import annotations

import json

import httplib2
import pytest
from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError

from gmail import client


def _http_error(status: int, reason: str | None = None) -> HttpError:
    """Build a googleapiclient HttpError like the API raises: an httplib2
    Response carrying the status + a JSON error body carrying the reason."""
    body: dict = {"error": {"status": "PERMISSION_DENIED"}}
    if reason is not None:
        body["error"]["errors"] = [{"reason": reason}]
    return HttpError(httplib2.Response({"status": status}),
                     json.dumps(body).encode("utf-8"))


@pytest.mark.parametrize(
    "exc, expected",
    [
        # Revoked / expired refresh token — the canonical reconnect trigger.
        (RefreshError("invalid_grant"), True),
        # 401 is always an auth failure regardless of reason.
        (_http_error(401, "authError"), True),
        (_http_error(401, None), True),
        # 403 only counts for genuine scope problems.
        (_http_error(403, "insufficientPermissions"), True),
        (_http_error(403, "ACCESS_TOKEN_SCOPE_INSUFFICIENT"), True),
        # 403s a reconnect would NOT fix → must stay False.
        (_http_error(403, "rateLimitExceeded"), False),
        (_http_error(403, "userRateLimitExceeded"), False),
        (_http_error(403, "quotaExceeded"), False),
        (_http_error(403, "accessNotConfigured"), False),
        (_http_error(403, None), False),
        # Other HTTP statuses are not reconnect signals.
        (_http_error(404, "notFound"), False),
        (_http_error(429, "rateLimitExceeded"), False),
        (_http_error(500, None), False),
        # Unrelated exceptions never trip a reconnect.
        (ValueError("boom"), False),
        (TimeoutError(), False),
    ],
)
def test_is_reauth_error(exc: BaseException, expected: bool) -> None:
    assert client.is_reauth_error(exc) is expected


def test_http_error_reason_parses_nested_reason() -> None:
    assert client.http_error_reason(_http_error(403, "insufficientPermissions")) \
        == "insufficientPermissions"


def test_http_error_reason_falls_back_to_status() -> None:
    # No errors[] array → fall back to error.status.
    assert client.http_error_reason(_http_error(403, None)) == "PERMISSION_DENIED"


def test_http_error_reason_handles_malformed_body() -> None:
    bad = HttpError(httplib2.Response({"status": 403}), b"not-json")
    assert client.http_error_reason(bad) == ""


def test_extract_attachments_picks_filed_parts_only() -> None:
    # A multipart message: text body, an inline image (no filename), and one real
    # attachment. Only the attachment (filename + attachmentId) should surface.
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {"mimeType": "text/plain", "body": {"data": "aGk="}},
            {"mimeType": "image/png", "filename": "",
             "body": {"attachmentId": "inline1", "size": 10}},
            {"mimeType": "application/pdf", "filename": "invoice.pdf",
             "body": {"attachmentId": "att-abc", "size": 2048}},
        ],
    }
    atts = client._extract_attachments(payload)
    assert atts == [{
        "filename": "invoice.pdf", "mime_type": "application/pdf",
        "size": 2048, "attachment_id": "att-abc",
    }]


def test_extract_attachments_walks_nested_parts() -> None:
    # Attachment nested under a multipart/related branch is still found.
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {"mimeType": "multipart/related", "parts": [
                {"mimeType": "application/zip", "filename": "data.zip",
                 "body": {"attachmentId": "z1", "size": None}},
            ]},
        ],
    }
    atts = client._extract_attachments(payload)
    assert len(atts) == 1
    assert atts[0]["filename"] == "data.zip"
    assert atts[0]["mime_type"] == "application/zip"
    assert atts[0]["size"] is None


def test_extract_attachments_empty_when_none() -> None:
    payload = {"mimeType": "text/plain", "body": {"data": "aGk="}}
    assert client._extract_attachments(payload) == []


def test_extract_headers_picks_triage_headers_lowercased() -> None:
    headers = [
        {"name": "From", "value": "a@b.com"},
        {"name": "Auto-Submitted", "value": "auto-generated"},
        {"name": "Precedence", "value": "bulk"},
        {"name": "List-Id", "value": "<news.example.com>"},
        {"name": "List-Unsubscribe", "value": "<https://x/unsub>"},
        {"name": "Subject", "value": "hi"},  # not a triage header
    ]
    out = client._extract_headers(headers)
    assert out == {
        "auto-submitted": "auto-generated",
        "precedence": "bulk",
        "list-id": "<news.example.com>",
        "list-unsubscribe": "<https://x/unsub>",
    }


def test_extract_headers_omits_absent() -> None:
    assert client._extract_headers([{"name": "From", "value": "a@b.com"}]) == {}
