"""Auto-follow applies to small group chats only."""
from __future__ import annotations

from fetcher import raw


def test_small_groups_only() -> None:
    assert raw.should_auto_follow("group", 4, 10)
    assert raw.should_auto_follow("supergroup", 10, 10)
    assert not raw.should_auto_follow("group", 11, 10)
    assert not raw.should_auto_follow("channel", 3, 10)       # broadcasts are not chats
    assert not raw.should_auto_follow("group", None, 10)      # unknown size: leave it
    assert not raw.should_auto_follow("group", 4, 0)          # setting off
