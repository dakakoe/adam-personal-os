"""An event ADAM writes to Google lands in the mirror with the worker's row shape."""
from __future__ import annotations

from datetime import datetime, timezone

from merge_api import gcal_write as g


def test_timed_event_row() -> None:
    ev = {"id": "abc", "summary": "Report", "status": "confirmed", "htmlLink": "https://cal/x",
          "start": {"dateTime": "2026-10-18T13:30:00+07:00"}, "end": {"dateTime": "2026-10-18T15:00:00+07:00"},
          "attendees": [{"email": "a@x.io", "responseStatus": "needsAction"},
                        {"email": "me@x.io", "self": True, "responseStatus": "accepted"}],
          "organizer": {"email": "me@x.io"}, "updated": "2026-10-18T05:53:14.000Z"}
    row = g.event_row("me@x.io", "primary", ev)
    assert row["event_id"] == "abc" and row["all_day"] is False
    assert row["start_ts"].isoformat().startswith("2026-10-18T13:30:00+07:00")
    assert row["self_response"] == "accepted" and row["organizer_email"] == "me@x.io"
    assert row["updated_ts"] == datetime(2026, 10, 18, 5, 53, 14, tzinfo=timezone.utc)
    assert row["payload"]["mirrored"] is True


def test_all_day_and_missing_start() -> None:
    row = g.event_row("me@x.io", "primary", {"id": "d", "start": {"date": "2026-10-19"}, "end": {"date": "2026-10-20"}})
    assert row["all_day"] is True and row["start_ts"].tzinfo is timezone.utc
    assert g.event_row("me@x.io", "primary", {"id": "x", "start": {}}) is None


def test_recurring_master_is_not_mirrored() -> None:
    ev = {"id": "m", "start": {"dateTime": "2026-10-18T06:00:00+07:00"}, "recurrence": ["RRULE:FREQ=DAILY"]}
    assert g.event_row("me@x.io", "primary", ev) is None
