"""Weak-labeling tests — the one piece with real logic that runs without torch.
A wrong labeling function silently poisons the whole training set, so pin the
category backbone, the header/keyword fallbacks, and the conflict-abstain guard.
"""
from __future__ import annotations

from mail_ml import labeling as L


def _wl(labels=None, subject="", body="", headers=None, from_address=None):
    return L.weak_label(labels=labels or [], subject=subject, body=body,
                        headers=headers, from_address=from_address)


def test_gmail_category_backbone():
    assert _wl(["CATEGORY_PROMOTIONS"], subject="hey") == L.NEWSLETTER
    assert _wl(["CATEGORY_UPDATES"], subject="hey") == L.TRANSACTIONAL
    assert _wl(["CATEGORY_SOCIAL"], subject="hey") == L.TRANSACTIONAL
    assert _wl(["CATEGORY_FORUMS"], subject="hey") == L.NEWSLETTER
    assert _wl(["CATEGORY_PERSONAL"], subject="hey") == L.PERSONAL


def test_conflict_abstains():
    # Gmail says promotions but the body is unmistakably a receipt → abstain.
    assert _wl(["CATEGORY_PROMOTIONS"], subject="Your order #12345 confirmation",
               body="receipt for your payment") is None
    # Gmail says updates but it's a blatant sale blast → abstain.
    assert _wl(["CATEGORY_UPDATES"], subject="50% off everything — shop now",
               body="huge sale this week, unsubscribe below") is None


def test_no_conflict_keeps_category():
    # promotions + marketing words (no txn) → stays newsletter.
    assert _wl(["CATEGORY_PROMOTIONS"], subject="Weekly digest", body="new arrivals") == L.NEWSLETTER
    # updates + txn words → stays transactional.
    assert _wl(["CATEGORY_UPDATES"], subject="Payment received", body="your invoice") == L.TRANSACTIONAL


def test_header_fallback_when_uncategorized():
    assert _wl([], subject="anything", headers={"list-unsubscribe": "<https://x/u>"}) == L.NEWSLETTER
    assert _wl([], subject="anything", headers={"auto-submitted": "auto-generated"}) == L.TRANSACTIONAL
    assert _wl([], subject="auto no", headers={"auto-submitted": "no"}) == L.PERSONAL


def test_header_signal_overrides_gmail_category():
    # The key fix: a List-Unsubscribe sender is a newsletter/subscription even when
    # Gmail filed it under Personal (that's why subscriptions leaked into Personal).
    assert _wl(["CATEGORY_PERSONAL"], subject="Our weekly update",
               headers={"list-unsubscribe": "<https://x/u>"}) == L.NEWSLETTER
    # A list message that's really a receipt still reads transactional.
    assert _wl(["CATEGORY_PERSONAL"], subject="Your order #5 shipped",
               body="delivery confirmation", headers={"list-id": "<orders.shop>"}) == L.TRANSACTIONAL


def test_keyword_fallback_when_uncategorized():
    assert _wl([], subject="Your receipt", body="order shipped") == L.TRANSACTIONAL
    assert _wl([], subject="Unsubscribe from our newsletter") == L.NEWSLETTER


def test_bare_human_mail_is_personal():
    assert _wl([], subject="lunch tomorrow?", body="are you free at noon",
               from_address="bob@example.org") == L.PERSONAL


def test_role_sender_never_personal():
    # The user's complaint: role/no-reply senders must not be 'personal'.
    assert _wl([], subject="Your invoice", from_address="billing@hetzner.com") == L.TRANSACTIONAL
    assert _wl([], subject="Sign-in", from_address="no_reply@email.apple.com") == L.TRANSACTIONAL
    # Even when Gmail filed it under Personal, a role sender is transactional.
    assert _wl(["CATEGORY_PERSONAL"], subject="Receipt",
               from_address="noreply@uber.com") == L.TRANSACTIONAL
    # A real person with the same Gmail category stays personal.
    assert _wl(["CATEGORY_PERSONAL"], subject="Re: coffee",
               from_address="bob@example.org") == L.PERSONAL


def test_is_role_sender_patterns():
    for a in ("no-reply@x.com", "noreply@x.com", "billing@hetzner.com",
              "notifications@github.com", "no_reply@email.apple.com", "team@x.com"):
        assert L._is_role_sender(a) is True, a
    for a in ("alice@example.com", "bob@example.org", "j.doe@acme.co"):
        assert L._is_role_sender(a) is False, a


def test_label_dataset_drops_abstentions():
    rows = [
        {"labels": ["CATEGORY_PERSONAL"], "subject": "hi", "body_text": "yo", "headers": {}},
        {"labels": ["CATEGORY_PROMOTIONS"], "subject": "Your receipt order #9",
         "body_text": "payment confirmation", "headers": {}},  # conflict → dropped
    ]
    pairs = L.label_dataset(rows)
    assert len(pairs) == 1
    assert pairs[0][1] == L.PERSONAL


def test_build_text_leads_with_sender_and_truncates():
    txt = L.build_text("Sub", "x" * 5000, "News@Brand.COM")
    assert txt.startswith("From: news@brand.com\nSub\nSub\n")  # sender lowercased, leads
    assert len(txt) < 1120   # body truncated to ~1000 chars


def test_build_text_handles_none():
    assert L.build_text(None, None) == "From:"
