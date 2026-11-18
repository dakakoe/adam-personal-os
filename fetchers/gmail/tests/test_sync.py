"""Read-state sync diff (round 2 / P4). The truncation guard is the piece with a
real failure cost: clearing UNREAD from a truncated Gmail list would silently
mark still-unread mail as read."""
from __future__ import annotations

from gmail.sync import read_state_diff


def test_read_elsewhere_clears_unread():
    # In our DB as unread, absent from Gmail's unread set → was read elsewhere.
    mark_read, mark_unread = read_state_diff(
        {"a", "b", "c"}, {"b"}, gmail_truncated=False)
    assert mark_read == ["a", "c"]
    assert mark_unread == []


def test_marked_unread_elsewhere_restores():
    # Gmail says unread, our copy doesn't → restore (SQL guards to existing rows).
    mark_read, mark_unread = read_state_diff(
        {"a"}, {"a", "x"}, gmail_truncated=False)
    assert mark_read == []
    assert mark_unread == ["x"]


def test_truncated_gmail_list_never_marks_read():
    # A capped (incomplete) Gmail list must not clear UNREAD by absence —
    # only the presence-based direction stays active.
    mark_read, mark_unread = read_state_diff(
        {"a", "b"}, {"b", "x"}, gmail_truncated=True)
    assert mark_read == []
    assert mark_unread == ["x"]


def test_in_sync_is_noop():
    assert read_state_diff({"a"}, {"a"}, gmail_truncated=False) == ([], [])
    assert read_state_diff(set(), set(), gmail_truncated=False) == ([], [])
