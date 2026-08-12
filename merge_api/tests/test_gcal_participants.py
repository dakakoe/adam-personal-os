"""eligible_invite_emails — the fail-closed filter deciding which routine
participants may actually be emailed a Google Calendar invite."""

from __future__ import annotations

from merge_api.gcal_write import eligible_invite_emails


def test_basic_pass_through():
    parts = [
        {"person_id": "1", "display_name": "A", "email": "a@x.com", "sensitive": False},
        {"person_id": "2", "display_name": "B", "email": "b@x.com", "sensitive": False},
    ]
    assert eligible_invite_emails(parts) == ["a@x.com", "b@x.com"]


def test_sensitive_is_never_invited():
    # fail-closed: a sensitive contact is dropped even with a valid email
    parts = [
        {"person_id": "1", "display_name": "A", "email": "a@x.com", "sensitive": True},
        {"person_id": "2", "display_name": "B", "email": "b@x.com", "sensitive": False},
    ]
    assert eligible_invite_emails(parts) == ["b@x.com"]


def test_no_email_is_skipped():
    parts = [
        {"person_id": "1", "display_name": "A", "email": None, "sensitive": False},
        {"person_id": "2", "display_name": "B", "email": "", "sensitive": False},
        {"person_id": "3", "display_name": "C", "email": "c@x.com", "sensitive": False},
    ]
    assert eligible_invite_emails(parts) == ["c@x.com"]


def test_dedup_case_insensitive_order_preserved():
    parts = [
        {"person_id": "1", "display_name": "A", "email": "Dup@X.com", "sensitive": False},
        {"person_id": "2", "display_name": "B", "email": "b@x.com", "sensitive": False},
        {"person_id": "3", "display_name": "C", "email": "dup@x.com", "sensitive": False},
    ]
    assert eligible_invite_emails(parts) == ["Dup@X.com", "b@x.com"]


def test_whitespace_email_trimmed_and_ignored_when_blank():
    parts = [
        {"person_id": "1", "display_name": "A", "email": "  a@x.com  ", "sensitive": False},
        {"person_id": "2", "display_name": "B", "email": "   ", "sensitive": False},
    ]
    assert eligible_invite_emails(parts) == ["a@x.com"]


def test_empty():
    assert eligible_invite_emails([]) == []
    # tolerate missing keys defensively
    assert eligible_invite_emails([{"person_id": "1", "display_name": "A"}]) == []
