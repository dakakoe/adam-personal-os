"""The response contract for a contact's message history.

Written after the page 404'd in production on its first real use. The query
was tested against a throwaway copy of the production schema and behaved
correctly — but the test called `queries.person_messages` directly, so the
row never met the response model, and `length(NULL) > 4000` → NULL → a
`truncated: bool` field that rejects None → 500 on any page holding a
captionless photo.

So these tests assert the SHAPE the endpoint promises, for the rows most
likely to break it: the ones where every optional column is NULL.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from merge_api.models import PersonMessage, PersonMessagesPage


def _row(**over):
    """A row as PERSON_MESSAGES_SQL returns it."""
    base = {
        "id": "8f1d4d3e-0000-4000-8000-000000000001",
        "occurred_at": datetime(2026, 9, 22, 9, 0, tzinfo=timezone.utc),
        "direction": "inbound",
        "channel": "telegram_text",
        "body": "hello",
        "truncated": False,
        "group_chat_id": None,
        "group_title": None,
    }
    return {**base, **over}


def test_a_captionless_photo_validates():
    # The row that took the page down: no body, so no length to compare.
    m = PersonMessage(**_row(channel="telegram_photo", body=None, truncated=False))
    assert m.body is None and m.truncated is False


def test_truncated_must_not_be_null():
    # Pinning the failure itself: if the SQL ever drops its COALESCE, this
    # fails here instead of 500-ing in production.
    with pytest.raises(ValidationError):
        PersonMessage(**_row(truncated=None))


def test_group_context_is_optional():
    assert PersonMessage(**_row(group_chat_id=None, group_title=None)).group_title is None
    m = PersonMessage(**_row(group_chat_id="-100123", group_title="A group"))
    assert m.group_chat_id == "-100123"


def test_a_page_of_mixed_rows_validates():
    page = PersonMessagesPage(
        total=3,
        channels=[{"channel": "telegram_text", "count": 2},
                  {"channel": "telegram_photo", "count": 1}],
        messages=[
            _row(),
            _row(id="…002", direction="outbound", body="x" * 4000, truncated=True),
            _row(id="…003", channel="telegram_photo", body=None),
        ],
    )
    assert page.total == 3
    assert [m.truncated for m in page.messages] == [False, True, False]


def test_an_empty_history_is_a_valid_page():
    page = PersonMessagesPage(total=0, channels=[], messages=[])
    assert page.messages == []
