"""Ollama refine tier — the verdict parser is the pure piece worth pinning
(a mis-parse would silently overwrite SetFit verdicts with garbage)."""
from __future__ import annotations

from mail_ml.ollama_tier import parse_verdict


def test_clean_json():
    assert parse_verdict('{"class": "transactional"}') == "transactional"
    assert parse_verdict('{"class": "Newsletter"}') == "newsletter"


def test_messy_text_falls_back_to_word():
    assert parse_verdict("I think this is a newsletter email.") == "newsletter"
    assert parse_verdict('{"class":"promo"} but really transactional') == "transactional"


def test_unusable_returns_none():
    assert parse_verdict("") is None
    assert parse_verdict('{"class": "spam"}') is None
    assert parse_verdict("no idea") is None
