"""Sensitivity routing — the draft prompt builder shared by cloud and local
paths, and the tolerant parser for the local model's reply."""
from __future__ import annotations

from merge_api.extraction import DRAFT_SYSTEM, build_draft_prompt, parse_local_draft


def test_build_prompt_includes_channel_context_and_history() -> None:
    p = build_draft_prompt(
        person_name="Alex", channel="email", self_label="Sam",
        recent_messages=[{"direction": "outbound", "body": "sent the deck"},
                         {"direction": "inbound", "body": "looks good"}],
        context_label="Task: Send revised pricing")
    assert "Draft a email message to: Alex" in p
    assert "Open item to follow up on: Task: Send revised pricing" in p
    assert "[Sam] sent the deck" in p
    assert "[Alex] looks good" in p
    assert p.endswith("Write the email draft now.")


def test_build_prompt_truncates_long_bodies_at_300() -> None:
    p = build_draft_prompt(
        person_name="X", channel="telegram", self_label="Me",
        recent_messages=[{"direction": "inbound", "body": "a" * 400}],
        context_label=None)
    assert "a" * 299 + "…" in p
    assert "a" * 300 not in p


def test_build_prompt_empty_history_branch() -> None:
    p = build_draft_prompt(person_name="X", channel="telegram", self_label="Me",
                           recent_messages=[], context_label=None)
    assert "No recent messages" in p


def test_parse_local_draft_valid_json() -> None:
    d = parse_local_draft('{"subject": "Hello", "body": "Hi there"}', "email")
    assert d == {"subject": "Hello", "body": "Hi there"}


def test_parse_local_draft_telegram_never_gets_subject() -> None:
    d = parse_local_draft('{"subject": "Hello", "body": "Hi"}', "telegram")
    assert d["subject"] is None
    assert d["body"] == "Hi"


def test_parse_local_draft_plain_text_fallback() -> None:
    d = parse_local_draft("Just a plain message body", "email")
    assert d == {"subject": None, "body": "Just a plain message body"}


def test_parse_local_draft_subject_line_split() -> None:
    d = parse_local_draft("Subject: Quick follow-up\nHey, checking in.", "email")
    assert d["subject"] == "Quick follow-up"
    assert d["body"] == "Hey, checking in."


def test_draft_system_still_guards_fabrication() -> None:
    assert "NEVER fabricate URLs" in DRAFT_SYSTEM