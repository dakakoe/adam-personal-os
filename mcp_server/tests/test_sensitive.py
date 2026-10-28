"""Every cloud-facing query hides contacts marked sensitive, and the by-id
tools give back nothing but a pointer to local_ask."""
from __future__ import annotations

from server import sensitive, tools

PEOPLE_QUERIES = {
    "search_people": tools.SEARCH_PEOPLE_SQL,
    "top_contacts": tools.TOP_CONTACTS_SQL,
    "semantic_search_people": tools.SEMANTIC_PEOPLE_SQL,
}
MESSAGE_QUERIES = {
    "search_messages": tools.SEARCH_MESSAGES_SQL,
    "semantic_search_messages": tools.SEMANTIC_MESSAGES_SQL,
    "semantic_search_messages(person_id)": tools.SEMANTIC_MESSAGES_BY_PERSON_SQL,
    "read_message": tools.READ_MESSAGE_SQL,
}


def test_every_people_query_filters_on_the_flag() -> None:
    for name, sql in PEOPLE_QUERIES.items():
        assert sensitive.person_clause() in sql, name


def test_every_message_query_hides_both_sides() -> None:
    for name, sql in MESSAGE_QUERIES.items():
        assert sensitive.INTERACTION_CLAUSE in sql, name


def test_mail_query_checks_every_address() -> None:
    assert sensitive.MAIL_CLAUSE in tools.SEMANTIC_MAIL_SQL
    assert sensitive.MAIL_CLAUSE in tools.READ_MAIL_SQL
    for col in ("from_address", "to_addresses", "cc_addresses"):
        assert col in sensitive.MAIL_CLAUSE


def test_null_sides_are_not_dropped() -> None:
    # `x IN (subquery)` is NULL for a NULL x; only COALESCE keeps such rows.
    assert "NOT IN" not in sensitive.INTERACTION_CLAUSE
    assert sensitive.INTERACTION_CLAUSE.count("COALESCE(") == 2


def test_redaction_carries_no_data() -> None:
    assert sensitive.redacted("abc") == {"person_id": "abc", "note": sensitive.NOTE}



def test_body_budget_is_clamped() -> None:
    assert tools._chars(0) == 500 and tools._chars(10) == 100
    assert tools._chars(4000) == 4000 and tools._chars(10**9) == tools.MAX_CHARS_CAP



def test_recent_interactions_includes_group_posts() -> None:
    sql = tools.RECENT_INTERACTIONS_SQL
    assert "author_person_id = $1::uuid" in sql and "in_group" in sql
    assert "raw.telegram_group_allowlist" in sql
