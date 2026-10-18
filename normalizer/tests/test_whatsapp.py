"""Pure-function tests for the WhatsApp normalizer.

These two functions decide who a message belongs to and what a new person is
called, so they are the ones worth pinning. Everything else in the module is
SQL, which the sql-smoke gate parses against the real schema.
"""

from __future__ import annotations

import pytest

from normalizer.whatsapp import CHANNEL_BY_KIND, chat_key_to_source_id, display_name_for


class TestChatKeyToSourceId:
    def test_phone_chat_key_is_the_source_id(self):
        assert chat_key_to_source_id("3725551234") == "3725551234"

    def test_lid_keeps_its_prefix(self):
        # The prefix is load-bearing: it is what stops a hidden id from being
        # mistaken for a phone number, and what the upgrade pass greps for.
        assert chat_key_to_source_id("lid:987654321") == "lid:987654321"

    def test_lid_and_phone_namespaces_never_collide(self):
        # The same digits arriving as a LID and as a number must not resolve
        # to one identity — they are only the same human once a mapping says
        # so, and that is the upgrade pass's decision, not ours.
        assert chat_key_to_source_id("lid:3725551234") != chat_key_to_source_id("3725551234")

    def test_group_has_no_person(self):
        # A group is not a counterparty. Returning None here is what makes
        # group interactions land with person_id NULL.
        assert chat_key_to_source_id("120363012345678901@g.us") is None

    @pytest.mark.parametrize("bad", [None, "", "not-digits", "372-555-1234", "372 555"])
    def test_rejects_anything_that_is_not_a_key(self, bad):
        assert chat_key_to_source_id(bad) is None


class TestDisplayName:
    def test_address_book_name_wins(self):
        # notify_name comes from the user's own phone book and is the only
        # high-trust name available.
        row = {
            "jid": "3725551234@s.whatsapp.net",
            "phone_e164": "3725551234",
            "notify_name": "Real Name",
            "push_name": "🔥 BEST DEALS 🔥",
            "business_name": None,
        }
        assert display_name_for(row) == "Real Name"

    def test_falls_back_to_push_name(self):
        row = {
            "jid": "3725551234@s.whatsapp.net",
            "phone_e164": "3725551234",
            "notify_name": None,
            "push_name": "Sasha",
            "business_name": None,
        }
        assert display_name_for(row) == "Sasha"

    def test_blank_names_are_not_names(self):
        row = {
            "jid": "3725551234@s.whatsapp.net",
            "phone_e164": "3725551234",
            "notify_name": "   ",
            "push_name": "",
            "business_name": None,
        }
        assert display_name_for(row) == "+3725551234"

    def test_lid_only_contact_still_gets_a_label(self):
        # No name and no number: the person must still be identifiable in a
        # list rather than appearing blank.
        row = {
            "jid": "987654321@lid",
            "phone_e164": None,
            "notify_name": None,
            "push_name": None,
            "business_name": None,
        }
        assert display_name_for(row) == "WhatsApp 987654321"


class TestChannelMap:
    def test_every_channel_is_prefixed(self):
        # The UI matches on the 'whatsapp' prefix rather than each suffix, so
        # a stray value would render as raw underscores in the follow-up list.
        for channel in CHANNEL_BY_KIND.values():
            assert channel.startswith("whatsapp_")

    def test_voice_is_distinct_from_audio(self):
        # Only whatsapp_voice is transcribed; collapsing these would either
        # send music to Whisper or leave voice notes without a body.
        assert CHANNEL_BY_KIND["voice"] != CHANNEL_BY_KIND["audio"]

    def test_unknown_kinds_have_a_home(self):
        assert CHANNEL_BY_KIND["other"] == "whatsapp_other"
