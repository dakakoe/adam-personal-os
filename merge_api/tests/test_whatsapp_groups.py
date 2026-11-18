"""WhatsApp group allowlist: sort whitelisting, the toggle body, and the
switched-off-stays-off rule that keeps auto-follow from undoing the owner."""
from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError

from merge_api import queries
from merge_api.models import AutoFollowIn, WhatsAppGroupToggleIn


def test_sort_param_never_reaches_sql_unvalidated() -> None:
    assert "g.member_count ASC" in queries.build_list_whatsapp_groups_sql("members_asc")
    hostile = queries.build_list_whatsapp_groups_sql("title; DROP TABLE raw.whatsapp_message")
    assert "DROP" not in hostile
    assert queries.WHATSAPP_GROUPS_SORTS[queries.WHATSAPP_GROUPS_SORT_DEFAULT] in hostile


def test_list_sql_carries_message_count_and_switched_off_flag() -> None:
    sql = queries.build_list_whatsapp_groups_sql("recent")
    assert "msg_count" in sql and "switched_off" in sql
    assert "LIMIT $3 OFFSET $4" in sql


def test_toggle_body_accepts_group_jids_only() -> None:
    ok = WhatsAppGroupToggleIn(chat_jid="120363012345678901@g.us", enabled=True)
    assert ok.enabled is True
    WhatsAppGroupToggleIn(chat_jid="447700900123-1612345678@g.us", enabled=False)  # legacy group id
    for bad in ("447700900123@s.whatsapp.net", "123@lid", "'; --@g.us", ""):
        with pytest.raises(ValidationError):
            WhatsAppGroupToggleIn(chat_jid=bad, enabled=True)


def test_switching_off_is_sticky_against_auto_follow() -> None:
    toggle = inspect.getsource(queries.toggle_whatsapp_group)
    assert "dismissed_at = CASE WHEN $2 THEN NULL ELSE now() END" in toggle
    follow = inspect.getsource(queries.auto_follow_small_whatsapp_groups)
    assert "dismissed_at IS NULL" in follow and "member_count <= $1" in follow


def test_auto_follow_limit_is_bounded() -> None:
    assert AutoFollowIn(max_members=9).max_members == 9
    with pytest.raises(ValidationError):
        AutoFollowIn(max_members=-1)
