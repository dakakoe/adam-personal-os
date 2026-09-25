"""You are not one of your own contacts.

A meeting recap names you the way it names everyone else — "Alex and Dana"
produces an action item whose person is "Alex" — and the name resolver
prefix-matches, so that reached a CONTACT sharing the first name and the task
was filed as being with a stranger.

The rule that makes this usable is whole-string matching: a bare first name is
you, anything more specific is the other person. Saying the full name is how
you mean the contact.
"""

from __future__ import annotations

import pytest

from merge_api.config import _self_names
from merge_api.queries import is_self_name, self_names_from_attendees

SELF = frozenset({"alex", "aleks", "alex rivera"})


class TestIsSelfName:
    @pytest.mark.parametrize("name", [
        "Alex", "alex", "  ALEX  ", "Aleks",
        "Alex Rivera",
        "Alex's", "Alex’s",   # transcripts produce possessives
        "Mr Alex", "Mr. Alex",
    ])
    def test_your_own_name_is_you(self, name):
        assert is_self_name(name, SELF)

    @pytest.mark.parametrize("name", [
        "Alex Chan",      # THE point: a fuller name still reaches the contact
        "Alex Brin",
        "Alec", "Alexander", "Dana", "", "   ", None,
    ])
    def test_everyone_else_is_a_contact(self, name):
        assert not is_self_name(name, SELF)

    def test_no_configured_names_never_matches(self):
        """Default install has no self_label set; nothing may be swallowed."""
        assert not is_self_name("Alex", frozenset())


class TestSelfNamesFromConfig:
    def test_first_name_is_derived_from_a_full_label(self):
        """A recap almost always writes the first name alone."""
        assert _self_names("Alex Rivera", "") == {"alex", "alex rivera"}

    def test_extra_aliases_are_added(self):
        got = _self_names("Alex", "Aleks, Алекс")
        assert got == {"alex", "aleks", "алекс"}

    @pytest.mark.parametrize("label", ["the user", "me", "the account owner", "", "  "])
    def test_placeholder_labels_yield_nothing(self, label):
        """These ship as defaults. Treating 'the user' as a name would blocklist
        a common English phrase and silently drop real matches."""
        assert _self_names(label, "") == frozenset()

    def test_a_phrase_label_contributes_no_first_token(self):
        """'the owner of the account' must not blocklist the word 'the'."""
        assert "the" not in _self_names("the owner of the account", "")


class TestSelfNamesFromAttendees:
    """The meeting names you itself.

    Granola lists the owner among the attendees like anyone else, so the
    attendee carrying one of your own inboxes tells us the exact spelling the
    recap will use — which a configured label can't. The real case: the label
    was "Alex" while every recap said "Alex Rivera".
    """

    OWN = {"owner@example.com", "owner.alt@example.com"}
    REAL = [  # the shape a real recap has
        {"name": "Alex Rivera", "email": "owner@example.com"},
        {"name": "Dana", "email": "dana@example.org"},
    ]

    def test_the_owner_attendee_names_you(self):
        got = self_names_from_attendees(self.REAL, self.OWN, frozenset({"alex"}))
        assert "alex rivera" in got
        assert "alex" in got          # first name, how a recap refers to you

    def test_other_attendees_are_never_self(self):
        got = self_names_from_attendees(self.REAL, self.OWN, frozenset())
        assert "dana" not in got

    def test_a_contact_sharing_your_first_name_still_resolves(self):
        """The whole point: Alex Chan was never in the call."""
        got = self_names_from_attendees(self.REAL, self.OWN, frozenset({"alex"}))
        assert is_self_name("Alex", got)
        assert is_self_name("Alex Rivera", got)
        assert not is_self_name("Alex Chan", got)

    def test_no_matching_attendee_falls_back_to_config(self):
        base = frozenset({"alex"})
        assert self_names_from_attendees(
            [{"name": "Dana", "email": "dana@example.org"}], self.OWN, base) == base

    def test_junk_attendees_are_ignored(self):
        assert self_names_from_attendees(
            [None, "a string", {}, {"name": "X"}, {"email": "owner@example.com"}],
            self.OWN, frozenset()) == frozenset()
