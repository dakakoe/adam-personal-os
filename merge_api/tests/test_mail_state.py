"""Mail polish — trash + snooze state plumbing.

Pins: the Gmail label ops for trash/untrash (incl. the archive+trash INBOX
dedupe), the tri-state snoozed_until wire semantics on MailStateIn (omitted vs
explicit null, via model_fields_set), the sender bulk-act mapping (regression
guard for the old trash→archived conflation), and MailThreadRow defaults for
rows that predate the new columns.
"""
from __future__ import annotations

from datetime import datetime

from merge_api import app as app_module
from merge_api.gmail_fetch import _state_label_ops
from merge_api.models import MailStateIn, MailThreadRow


def test_label_ops_trash() -> None:
    assert _state_label_ops(None, None, trashed=True) == (["TRASH"], ["INBOX"])


def test_label_ops_untrash() -> None:
    assert _state_label_ops(None, None, trashed=False) == (["INBOX"], ["TRASH"])


def test_label_ops_archive_plus_trash_dedupes_inbox() -> None:
    add, remove = _state_label_ops(True, None, trashed=True)
    assert add == ["TRASH"]
    assert remove == ["INBOX"]          # once, not twice


def test_label_ops_unarchive_plus_trash_trash_wins_on_inbox() -> None:
    # Contradictory input (unarchive + trash): trash's INBOX removal wins;
    # Gmail rejects the same label in both lists.
    add, remove = _state_label_ops(False, None, trashed=True)
    assert "INBOX" not in add
    assert add == ["TRASH"]
    assert remove == ["INBOX"]


def test_label_ops_untrash_restores_inbox_over_archive() -> None:
    add, remove = _state_label_ops(True, None, trashed=False)
    assert "INBOX" in add
    assert "INBOX" not in remove
    assert "TRASH" in remove


def test_label_ops_existing_behavior_unchanged() -> None:
    assert _state_label_ops(True, True) == (["STARRED"], ["INBOX"])
    assert _state_label_ops(None, None) == ([], [])


def test_mail_state_in_snooze_tristate() -> None:
    omitted = MailStateIn(account_email="a@b.c", thread_key="t1")
    assert "snoozed_until" not in omitted.model_fields_set
    assert omitted.snoozed_until is None

    explicit_null = MailStateIn.model_validate(
        {"account_email": "a@b.c", "thread_key": "t1", "snoozed_until": None})
    assert "snoozed_until" in explicit_null.model_fields_set
    assert explicit_null.snoozed_until is None


def test_mail_state_in_snooze_parses_tz_aware() -> None:
    m = MailStateIn.model_validate(
        {"account_email": "a@b.c", "thread_key": "t1",
         "snoozed_until": "2026-07-03T09:00:00+02:00"})
    assert isinstance(m.snoozed_until, datetime)
    assert m.snoozed_until.tzinfo is not None


def test_sender_act_trash_sets_trashed_not_archived() -> None:
    # Regression guard: sender-act trash used to conflate into archived=True,
    # which would hide bulk-trashed mail from the Trash view.
    assert app_module._SENDER_ACT_OVERLAY["trash"] == {"trashed": True}
    assert app_module._SENDER_ACT_OVERLAY["archive"] == {"archived": True}
    assert app_module._SENDER_ACT_LABELS["trash"] == (["TRASH"], ["INBOX"])


def test_mail_thread_row_defaults_for_old_rows() -> None:
    row = MailThreadRow(thread_key="t1", account_email="a@b.c",
                        internal_date=datetime(2026, 7, 1))
    assert row.trashed is False
    assert row.snoozed_until is None
