"""Tests for WhatsApp's own "Export Chat" text format.

The format is barely specified and varies by platform and locale, so these
pin the behaviour that would otherwise fail silently: ambiguous dates, the
invisible characters WhatsApp embeds, multi-line messages, and system notices
that are not messages at all.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from whatsapp_import.chatexport import (
    detect_timestamp_format,
    parse,
    synthetic_id,
)

SELF = "the user"
CHAT_KEY = "3725551234"
CHAT_JID = "3725551234@s.whatsapp.net"


def _parse(text, **kw):
    return list(parse(text, chat_key=CHAT_KEY, chat_jid=CHAT_JID, self_name=SELF, **kw))


IOS = """[15/03/2024, 10:23:45] Alice: hello there
[15/03/2024, 10:24:01] the user: hi back
"""


class TestFormatDetection:
    def test_day_first_disambiguated_by_a_day_over_twelve(self):
        # 15 cannot be a month, so this file is unambiguously day-first.
        assert detect_timestamp_format(["15/03/2024, 10:23:45"]) == "%d/%m/%Y, %H:%M:%S"

    def test_chronological_order_breaks_the_tie(self):
        # Both readings parse, but only day-first keeps the export ascending.
        # Month-first would read these as 3 Jan, 4 Jan, 2 Feb — still ascending
        # — so use a sequence where it is not.
        samples = ["05/03/2024, 10:00:00", "06/03/2024, 10:00:00", "07/03/2024, 10:00:00"]
        fmt = detect_timestamp_format(samples)
        parsed = [datetime.strptime(s, fmt) for s in samples]
        assert parsed == sorted(parsed)

    def test_refuses_to_guess_when_nothing_fits(self):
        # Returning None makes the importer stop and ask, rather than writing
        # dates that are quietly months wrong.
        assert detect_timestamp_format(["not a date"]) is None

    def test_android_dash_layout(self):
        msgs = _parse("15/03/2024, 10:23 - Alice: hi\n")
        assert len(msgs) == 1
        assert msgs[0]["text"] == "hi"


class TestParsing:
    def test_direction_from_the_sender_name(self):
        msgs = _parse(IOS)
        assert [m["from_me"] for m in msgs] == [False, True]
        assert msgs[0]["sender_jid"] == CHAT_JID
        assert msgs[1]["sender_jid"] is None

    def test_invisible_marks_do_not_break_the_line(self):
        # WhatsApp embeds LTR marks; a naive regex misses every line.
        msgs = _parse("‎[15/03/2024, 10:23:45] Alice: hi\n")
        assert len(msgs) == 1 and msgs[0]["text"] == "hi"

    def test_multiline_message_keeps_its_tail(self):
        text = (
            "[15/03/2024, 10:23:45] Alice: first line\n"
            "second line\n"
            "third line\n"
            "[15/03/2024, 10:24:00] the user: ok\n"
        )
        msgs = _parse(text)
        assert msgs[0]["text"] == "first line\nsecond line\nthird line"
        assert len(msgs) == 2

    def test_system_notices_are_not_messages(self):
        text = (
            "[15/03/2024, 10:00:00] Messages and calls are end-to-end encrypted.\n"
            "[15/03/2024, 10:23:45] Alice: hi\n"
        )
        msgs = _parse(text)
        assert len(msgs) == 1
        assert msgs[0]["text"] == "hi"

    def test_attachments_become_the_right_kind_with_no_text(self):
        text = (
            "[15/03/2024, 10:00:00] Alice: audio omitted\n"
            "[15/03/2024, 10:01:00] Alice: image omitted\n"
            "[15/03/2024, 10:02:00] Alice: ‎<attached: 0001-PHOTO-2024.jpg>\n"
            "[15/03/2024, 10:03:00] Alice: ‎<attached: 0002-AUDIO-2024.opus>\n"
        )
        kinds = [m["kind"] for m in _parse(text)]
        assert kinds == ["voice", "image", "image", "voice"]
        assert all(m["text"] is None for m in _parse(text))

    def test_message_dates_are_utc_iso(self):
        m = _parse(IOS)[0]
        assert m["message_date"].startswith("2024-03-15T10:23:45")

    def test_unparseable_timestamps_raise_rather_than_guess(self):
        with pytest.raises(ValueError):
            _parse("[not a date] Alice: hi\n")


class TestSyntheticIds:
    def test_deterministic_so_reimport_is_a_no_op(self):
        a = _parse(IOS)
        b = _parse(IOS)
        assert [m["source_message_id"] for m in a] == [m["source_message_id"] for m in b]

    def test_prefixed_so_it_cannot_be_mistaken_for_a_real_id(self):
        # A real WhatsApp id is hex; this must never collide with one.
        for m in _parse(IOS):
            assert m["source_message_id"].startswith("exp:")

    def test_different_content_gives_different_ids(self):
        t = datetime(2024, 3, 15, 10, 0)
        assert synthetic_id(CHAT_KEY, t, "Alice", "a") != synthetic_id(CHAT_KEY, t, "Alice", "b")
        assert synthetic_id(CHAT_KEY, t, "Alice", "a") != synthetic_id("other", t, "Alice", "a")

    def test_same_text_at_different_times_is_distinct(self):
        # "ok" appears constantly; collapsing them would lose real messages.
        text = (
            "[15/03/2024, 10:00:00] Alice: ok\n"
            "[15/03/2024, 11:00:00] Alice: ok\n"
        )
        ids = {m["source_message_id"] for m in _parse(text)}
        assert len(ids) == 2


class TestGroups:
    def test_group_sender_is_unknown_because_exports_have_no_numbers(self):
        msgs = list(parse(
            "[15/03/2024, 10:00:00] Bob: hi all\n",
            chat_key="120363@g.us", chat_jid="120363@g.us",
            self_name=SELF, is_group=True,
        ))
        assert msgs[0]["is_group"] is True
        # Honest: a name is not an identity, so we record no sender.
        assert msgs[0]["sender_jid"] is None
        assert msgs[0]["payload"]["sender_name"] == "Bob"


class TestFilenameParsing:
    """The export filename is the only clue to who a chat is with."""

    def test_extracts_the_contact_name(self):
        from whatsapp_import.__main__ import chat_name_from_filename
        assert chat_name_from_filename("WhatsApp Chat with Alice") == "Alice"
        assert chat_name_from_filename("WhatsApp Chat with Alice Smith") == "Alice Smith"
        # Case and spacing vary between iOS versions and locales.
        assert chat_name_from_filename("whatsapp chat with  Bob ") == "Bob"

    def test_falls_back_to_the_whole_stem(self):
        # An unrecognised name is still worth showing in the map file, so the
        # user can see which file a row refers to.
        from whatsapp_import.__main__ import chat_name_from_filename
        assert chat_name_from_filename("Alice") == "Alice"

    def test_group_names_survive(self):
        from whatsapp_import.__main__ import chat_name_from_filename
        assert chat_name_from_filename("WhatsApp Chat with Founders Group") == "Founders Group"
