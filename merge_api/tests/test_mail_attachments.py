"""Mail Phase 3b — attachment metadata parser in merge_api.gmail_fetch.

This is a deliberate second copy of the fetcher's extractor (merge_api can't
import the fetcher package), so it gets its own pin: only real attachments
(filename + attachmentId) surface, nested parts are walked, inline parts are
skipped.
"""
from __future__ import annotations

from merge_api import gmail_fetch, queries, rspamd


def test_extract_attachments_filed_parts_only() -> None:
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {"mimeType": "text/plain", "body": {"data": "aGk="}},
            # inline image: has attachmentId but no filename → skipped
            {"mimeType": "image/png", "filename": "",
             "body": {"attachmentId": "inline1", "size": 10}},
            {"mimeType": "application/pdf", "filename": "invoice.pdf",
             "body": {"attachmentId": "att-abc", "size": 2048}},
        ],
    }
    assert gmail_fetch.extract_attachments(payload) == [{
        "filename": "invoice.pdf", "mime_type": "application/pdf",
        "size": 2048, "attachment_id": "att-abc",
    }]


def test_extract_attachments_walks_nested() -> None:
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {"mimeType": "multipart/related", "parts": [
                {"mimeType": "application/zip", "filename": "data.zip",
                 "body": {"attachmentId": "z1", "size": None}},
            ]},
        ],
    }
    atts = gmail_fetch.extract_attachments(payload)
    assert len(atts) == 1
    assert atts[0]["filename"] == "data.zip"
    assert atts[0]["mime_type"] == "application/zip"
    assert atts[0]["size"] is None


def test_extract_attachments_none() -> None:
    assert gmail_fetch.extract_attachments(
        {"mimeType": "text/plain", "body": {"data": "aGk="}}) == []


def test_extract_attachments_defaults_mime() -> None:
    # A filed part with no mimeType falls back to octet-stream.
    payload = {"filename": "x.bin", "body": {"attachmentId": "a1", "size": 3}}
    out = gmail_fetch.extract_attachments(payload)
    assert out[0]["mime_type"] == "application/octet-stream"


# --- Phase 3c: app archive/star state → Gmail label ops --------------------

def test_state_label_ops_archive_removes_inbox() -> None:
    assert gmail_fetch._state_label_ops(True, None) == ([], ["INBOX"])


def test_state_label_ops_unarchive_adds_inbox() -> None:
    assert gmail_fetch._state_label_ops(False, None) == (["INBOX"], [])


def test_state_label_ops_star_adds_starred() -> None:
    assert gmail_fetch._state_label_ops(None, True) == (["STARRED"], [])


def test_state_label_ops_unstar_removes_starred() -> None:
    assert gmail_fetch._state_label_ops(None, False) == ([], ["STARRED"])


def test_state_label_ops_combined() -> None:
    # Archive + star in one call: remove INBOX, add STARRED.
    add, remove = gmail_fetch._state_label_ops(True, True)
    assert add == ["STARRED"] and remove == ["INBOX"]


def test_state_label_ops_noop_when_unset() -> None:
    # Neither field provided → no label changes (push is skipped upstream).
    assert gmail_fetch._state_label_ops(None, None) == ([], [])


# --- Phase B: header-heuristic triage signals -------------------------------

def test_triage_signals_empty_headers() -> None:
    s = queries.triage_signals(None)
    assert s == {"automated": False, "bulk": False, "mailing_list": False,
                 "has_unsubscribe": False, "unsubscribe_url": None}


def test_triage_signals_auto_submitted() -> None:
    # RFC 3834: any value other than 'no' marks automated.
    assert queries.triage_signals({"auto-submitted": "auto-generated"})["automated"] is True
    assert queries.triage_signals({"auto-submitted": "no"})["automated"] is False


def test_triage_signals_bulk_precedence() -> None:
    for v in ("bulk", "list", "junk"):
        assert queries.triage_signals({"precedence": v})["bulk"] is True
    assert queries.triage_signals({"precedence": "normal"})["bulk"] is False


def test_triage_signals_mailing_list_and_unsubscribe() -> None:
    s = queries.triage_signals({
        "list-id": "<news.example.com>",
        "list-unsubscribe": "<mailto:u@x>, <https://x/unsub?id=1>",
    })
    assert s["mailing_list"] is True
    assert s["has_unsubscribe"] is True
    # Only the https link is surfaced for one-click (never mailto).
    assert s["unsubscribe_url"] == "https://x/unsub?id=1"


def test_triage_signals_unsubscribe_mailto_only_no_url() -> None:
    s = queries.triage_signals({"list-unsubscribe": "<mailto:unsub@x>"})
    assert s["has_unsubscribe"] is True
    assert s["unsubscribe_url"] is None


def test_extract_headers_from_payload_lowercased() -> None:
    payload = {"headers": [
        {"name": "From", "value": "a@b.com"},
        {"name": "Precedence", "value": "bulk"},
        {"name": "List-Id", "value": "<l>"},
    ]}
    assert gmail_fetch.extract_headers(payload) == {"precedence": "bulk", "list-id": "<l>"}


# --- Phase B2: Rspamd verdict parsing --------------------------------------

def test_rspamd_parse_flattens_symbols() -> None:
    body = {
        "score": 8.4, "action": "add header",
        "symbols": {
            "BAYES_SPAM": {"name": "BAYES_SPAM", "score": 5.1, "options": ["100%"]},
            "MIME_HTML_ONLY": {"name": "MIME_HTML_ONLY", "score": 0.3},
        },
    }
    out = rspamd._parse(body)
    assert out["score"] == 8.4
    assert out["action"] == "add header"
    assert out["symbols"] == {"BAYES_SPAM": 5.1, "MIME_HTML_ONLY": 0.3}


def test_rspamd_parse_defaults_no_action() -> None:
    out = rspamd._parse({"score": 0.0})
    assert out["action"] == "no action"
    assert out["score"] == 0.0
    assert out["symbols"] == {}


def test_rspamd_parse_missing_score_is_none() -> None:
    assert rspamd._parse({"action": "reject"})["score"] is None


def test_rspamd_is_spammy() -> None:
    for a in ("reject", "add header", "rewrite subject", "soft reject", "ADD HEADER"):
        assert rspamd.is_spammy(a) is True
    for a in ("no action", "greylist", None, ""):
        assert rspamd.is_spammy(a) is False


def test_mail_is_spam_reject_only_and_dmarc_allowlist() -> None:
    # Gmail's own SPAM label always counts.
    assert queries._mail_is_spam(["SPAM"], "no action", None) is True
    # Low-confidence Rspamd bands are NOT spam (precision-first).
    assert queries._mail_is_spam([], "add header", None) is False
    assert queries._mail_is_spam([], "soft reject", None) is False
    # A high-confidence reject IS spam...
    assert queries._mail_is_spam([], "reject", None) is True
    assert queries._mail_is_spam([], "reject", {"authentication-results": "spf=fail"}) is True
    # ...unless the sender is DMARC-authenticated (legit, Rspamd over-flagged).
    assert queries._mail_is_spam([], "reject",
                                 {"authentication-results": "dkim=pass; dmarc=pass"}) is False


def test_dmarc_authenticated() -> None:
    assert queries._dmarc_authenticated({"authentication-results": "... dmarc=pass ..."}) is True
    assert queries._dmarc_authenticated({"authentication-results": "dmarc=fail"}) is False
    assert queries._dmarc_authenticated({}) is False
    assert queries._dmarc_authenticated(None) is False
