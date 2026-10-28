"""Pure-function tests for the iPhone contacts pass: where a card goes, what
counts as a match, and which names may be replaced. The rest is SQL.

All identifiers are invented (555 numbers, example.com)."""
from __future__ import annotations

from normalizer.iphone_contacts import Maps, best_person, decide, is_placeholder_name, match_card


class TestDecide:
    def test_no_match_creates_a_person(self):
        d = decide(None, [], [])
        assert (d.action, d.person_id, d.review) == ("create", None, ())

    def test_one_strong_match_attaches(self):
        d = decide(None, ["p1"], [])
        assert (d.action, d.person_id, d.review) == ("attach", "p1", ())

    def test_several_strong_matches_join_the_one_with_most_history(self):
        # One human split across WhatsApp and Telegram: join the fuller
        # record, raise the other against it — never a third person.
        d = decide(None, ["p_tg", "p_wa"], [], history={"p_wa": 40, "p_tg": 3})
        assert (d.action, d.person_id, d.review) == ("attach", "p_wa", ("p_tg",))

    def test_several_strong_matches_without_history_pick_stably(self):
        first = decide(None, ["p2", "p1", "p3"], [])
        again = decide(None, ["p3", "p1", "p2"], [])
        assert first == again
        assert (first.action, first.person_id, first.review) == ("attach", "p1", ("p2", "p3"))

    def test_a_shared_number_alone_never_attaches(self):
        # One person matched, but only through a number on two cards: review.
        d = decide(None, [], ["p1"])
        assert (d.action, d.review) == ("create_and_review", ("p1",))

    def test_strong_match_attaches_and_weak_ones_are_reviewed(self):
        d = decide(None, ["p1"], ["p2"])
        assert (d.action, d.person_id, d.review) == ("attach", "p1", ("p2",))

    def test_reupload_stays_put_and_raises_others_for_review(self):
        d = decide("own", ["own", "p1"], ["p2"])
        assert (d.action, d.person_id, d.review) == ("keep", "own", ("p1", "p2"))

    def test_reupload_matching_only_itself_needs_no_review(self):
        assert decide("own", ["own"], []).review == ()


def test_best_person_prefers_history_then_id():
    assert best_person(["b", "a"], {"b": 5}) == "b"
    assert best_person(["b", "a"], {}) == "a"
    assert best_person(["b", "a"]) == "a"


class TestMatchCard:
    def _maps(self):
        return Maps(
            own={"uid-1": "own"},
            email={"dana@example.com": "p_email"},
            whatsapp={"12015550100": "p_wa"},
            telegram={"12015550111": {"p_tg"}},
            signals={"12015550122": {"p_sig"}},
        )

    def test_each_evidence_kind_matches(self):
        own, strong, weak = match_card(
            "uid-1", ["dana@example.com"], ["12015550100", "12015550111", "12015550122"],
            self._maps(), shared_numbers=set())
        assert own == "own"
        assert strong == {"p_email", "p_wa", "p_tg", "p_sig"} and weak == set()

    def test_shared_numbers_only_produce_weak_matches(self):
        _, strong, weak = match_card("uid-2", [], ["12015550100"], self._maps(), {"12015550100"})
        assert strong == set() and weak == {"p_wa"}

    def test_weak_excludes_people_already_matched_strongly(self):
        maps = self._maps()
        maps.signals["12015550199"] = {"p_email"}
        _, strong, weak = match_card("uid-2", ["dana@example.com"], ["12015550199"], maps, {"12015550199"})
        assert strong == {"p_email"} and weak == set()

    def test_unknown_card_matches_nobody(self):
        own, strong, weak = match_card("uid-9", ["x@example.com"], ["12015550999"], self._maps(), set())
        assert (own, strong, weak) == (None, set(), set())


class TestPlaceholderName:
    def test_placeholders(self):
        assert is_placeholder_name(None)
        assert is_placeholder_name("  ")
        assert is_placeholder_name("+1 (201) 555-0100")
        assert is_placeholder_name("12015550100")
        assert is_placeholder_name("Telegram user 12345")
        assert is_placeholder_name("dana@example.com")
        assert is_placeholder_name("Dana Lee", ["dana.lee@example.com"])
        assert is_placeholder_name("Unknown contact")

    def test_real_names_are_kept(self):
        assert not is_placeholder_name("Dana Lee")
        assert not is_placeholder_name("Dana Lee", ["dana@example.com"])
        assert not is_placeholder_name("🌸 Masha")
