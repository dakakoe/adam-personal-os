"""Attendee extraction — the part that decides who a meeting belongs to.

Everything else in meetings.py is SQL, checked by sql-smoke against the real
schema. These cover the parsing that would otherwise silently attach a call to
the wrong person, or to a conference room.
"""

from __future__ import annotations

import pytest

from normalizer.meetings import (
    _as_list,
    _clean_email,
    _gcal_attendees,
    _granola_attendees,
)


class TestCleanEmail:
    def test_lowercases_and_trims(self):
        # Email identities are stored lowercased, so anything else never matches.
        assert _clean_email("  Alex@Example.COM ") == "alex@example.com"

    def test_rejects_non_addresses(self):
        for bad in [None, "", "   ", "not-an-email", 42, {"a": 1}]:
            assert _clean_email(bad) is None

    def test_rooms_and_equipment_are_not_people(self):
        # Google lists meeting rooms as attendees. Left in, every recurring
        # standup would invent a "person" for the room and attach calls to it.
        assert _clean_email("c_188abc@resource.calendar.google.com") is None
        assert _clean_email("room-4@resource.calendar.google.com") is None
        assert _clean_email("team@group.calendar.google.com") is None

    def test_ordinary_address_survives(self):
        assert _clean_email("dana@example.org") == "dana@example.org"


class TestGranolaAttendees:
    def test_reads_name_and_email(self):
        got = list(_granola_attendees([{"name": "Dana", "email": "dana@example.org"}]))
        assert got == [("dana@example.org", "Dana")]

    def test_missing_name_is_fine(self):
        got = list(_granola_attendees([{"email": "dana@example.org"}]))
        assert got == [("dana@example.org", None)]

    def test_entries_without_an_email_are_dropped(self):
        # A name alone is not an identity; guessing from it would attach a call
        # to whoever happens to share the name.
        got = list(_granola_attendees([{"name": "Dana"}, {"email": "x@y.com"}]))
        assert got == [("x@y.com", None)]

    @pytest.mark.parametrize("junk", [None, [], ["a string"], [None], [42]])
    def test_junk_is_survivable(self, junk):
        assert list(_granola_attendees(junk)) == []


class TestGcalAttendees:
    def test_reads_google_shape(self):
        got = list(_gcal_attendees([
            {"email": "dana@example.org", "displayName": "Dana", "responseStatus": "accepted"},
        ]))
        assert got == [("dana@example.org", "Dana", False)]

    def test_self_flag_is_carried(self):
        # Google marks your own attendee row; without it you become a
        # participant in every one of your own meetings.
        got = list(_gcal_attendees([{"email": "me@example.com", "self": True}]))
        assert got == [("me@example.com", None, True)]

    def test_resources_are_skipped_by_flag_too(self):
        got = list(_gcal_attendees([
            {"email": "room@example.com", "resource": True},
            {"email": "dana@example.org"},
        ]))
        assert [g[0] for g in got] == ["dana@example.org"]

    @pytest.mark.parametrize("junk", [None, [], ["a string"], [None]])
    def test_junk_is_survivable(self, junk):
        assert list(_gcal_attendees(junk)) == []


class TestJsonbArrivesAsAString:
    """The bug this file did not catch the first time.

    asyncpg hands jsonb back as a STRING in these pools — no codec is
    registered. Iterating that string yields characters, every one fails the
    dict check, and every attendee list silently comes back empty: 983
    meetings produced 0 participants and the run still exited 0.

    Fixtures that pass Python lists cannot see this. These pass strings.
    """

    def test_granola_attendees_from_a_json_string(self):
        raw = '[{"name": "Dana", "email": "dana@example.org"}]'
        assert list(_granola_attendees(raw)) == [("dana@example.org", "Dana")]

    def test_gcal_attendees_from_a_json_string(self):
        raw = '[{"email": "dana@example.org", "self": false}]'
        assert list(_gcal_attendees(raw)) == [("dana@example.org", None, False)]

    def test_as_list_handles_both_forms(self):
        assert _as_list('[{"a": 1}]') == [{"a": 1}]
        assert _as_list([{"a": 1}]) == [{"a": 1}]

    @pytest.mark.parametrize("junk", ["not json", "{}", '"a string"', "", None, 42])
    def test_as_list_never_raises(self, junk):
        assert _as_list(junk) == []
