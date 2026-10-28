"""Group message authors are recorded once, and only when they are users."""
from __future__ import annotations

from telethon.tl import types

from fetcher import raw


def _user(uid: int) -> types.User:
    return types.User(id=uid, first_name="A")


def test_only_users_and_only_once() -> None:
    seen: set = set()
    assert raw.should_record_sender(_user(1), seen)
    assert not raw.should_record_sender(_user(1), seen)          # already recorded this run
    assert raw.should_record_sender(_user(2), seen)
    assert not raw.should_record_sender(None, seen)
    assert not raw.should_record_sender(types.Channel(id=5, title="c", photo=None, date=None), seen)


def test_seen_set_is_bounded() -> None:
    seen = set(range(20001))
    assert raw.should_record_sender(_user(999_999), seen)
    assert len(seen) == 1
