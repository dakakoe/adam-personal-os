"""Pure-function guards for the LinkedIn capture path.

The identity-resolution SQL needs a database, but the normalization in front of
it decides whether two captures of the same person collide or fork into
duplicates — vanity casing, a full name that has to split into first/last, and
the email/phone/host cleanups feeding canonical.identity and
memory.extracted_signal. Those are pure and cheap to pin down here."""

from __future__ import annotations

import pytest

from merge_api import queries as q


class TestNormalizeVanity:
    @pytest.mark.parametrize("value", [
        "https://www.linkedin.com/in/ada-lovelace",
        "https://www.linkedin.com/in/ada-lovelace/",
        "https://linkedin.com/in/Ada-Lovelace/?originalSubdomain=uk",
        "http://www.linkedin.com/in/ADA-LOVELACE/detail/contact-info/",
        "linkedin.com/in/ada-lovelace",
        "ada-lovelace",
        "  Ada-Lovelace  ",
    ])
    def test_every_shape_collapses_to_one_vanity(self, value):
        """Dedup lives or dies here: the extension may send a full URL, the CSV
        importer stores one, and a human may type the bare handle."""
        assert q.normalize_linkedin_vanity(value) == "ada-lovelace"

    @pytest.mark.parametrize("value", [None, "", "   ", "/", "https://example.com/",
                                       "not a vanity", "in/ada lovelace"])
    def test_junk_is_rejected(self, value):
        assert q.normalize_linkedin_vanity(value) is None

    def test_percent_escapes_survive_undecoded(self):
        """Non-latin vanities arrive percent-encoded. The CSV normalizer stores
        them that way, so decoding here would break the join."""
        assert q.normalize_linkedin_vanity(
            "https://www.linkedin.com/in/%D0%B0%D0%B4%D0%B0") == "%d0%b0%d0%b4%d0%b0"


class TestSplitPersonName:
    def test_explicit_first_last_win(self):
        assert q.split_person_name("Ada L.", "Ada", "Lovelace") == ("Ada L.", "Ada", "Lovelace")

    def test_full_name_splits_on_first_space(self):
        assert q.split_person_name("Ada Byron Lovelace", None, None) == (
            "Ada Byron Lovelace", "Ada", "Byron Lovelace")

    def test_single_token_name_has_no_last(self):
        assert q.split_person_name("Madonna", None, None) == ("Madonna", "Madonna", None)

    def test_first_last_compose_the_display_name(self):
        assert q.split_person_name(None, "Ada", "Lovelace") == ("Ada Lovelace", "Ada", "Lovelace")

    def test_falls_back_to_the_vanity(self):
        """A profile whose h1 didn't parse still becomes a person — named after
        the vanity, which is_synthetic_display_name will later let a real name
        overwrite."""
        assert q.split_person_name(None, None, None, fallback="ada-lovelace") == (
            "ada-lovelace", None, None)

    def test_last_resort_is_unknown(self):
        assert q.split_person_name("  ", None, None)[0] == "Unknown"


class TestValueCleanups:
    @pytest.mark.parametrize("raw,expected", [
        ("Ada@Example.COM", "ada@example.com"),
        ("  ada@example.com ", "ada@example.com"),
        ("not-an-email", None),
        ("two words@example.com", None),
        (None, None),
    ])
    def test_emails(self, raw, expected):
        assert q._clean_email(raw) == expected

    @pytest.mark.parametrize("raw,expected", [
        ("+44 (0) 7700 900123", "4407700900123"),
        ("07700-900123", "07700900123"),
        ("n/a", None),
        (None, None),
    ])
    def test_phones_reduce_to_digits(self, raw, expected):
        """Matches normalizer.linkedin's phone handling, so a phone captured
        here and the same phone from the CSV import land on one signal row."""
        assert q._clean_phone(raw) == expected

    @pytest.mark.parametrize("raw,expected", [
        ("https://www.ada.dev/about?x=1", "ada.dev"),
        ("http://ada.dev", "ada.dev"),
        ("ada.dev/blog", "ada.dev"),
        ("localhost", None),
        (None, None),
    ])
    def test_websites_reduce_to_bare_host(self, raw, expected):
        assert q._clean_host(raw) == expected

    def test_dedupe_keeps_first_occurrence_and_drops_empties(self):
        assert q._dedupe(["a", None, "b", "a", "", "c"]) == ["a", "b", "c"]
