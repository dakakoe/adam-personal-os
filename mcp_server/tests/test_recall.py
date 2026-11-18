"""recall.py — answer key and hit counting behind the find-people eval."""
from __future__ import annotations

from server.recall import answer_key, hits_at, regressions

MEMBERS = {
    "investors": {"a", "b", "s"},
    "exchanges": {"b", "c"},
    "blocked": {"s", "x"},
}
# What the user configured as "never suggest these people" — a key from their
# own circles, passed in, never a literal in the module under test.
BLOCKED = ("blocked",)


def test_key_unions_requested_circles() -> None:
    assert answer_key(["exchanges"], MEMBERS, BLOCKED) == {"b", "c"}
    assert answer_key(["investors", "exchanges"], MEMBERS, BLOCKED) == {"a", "b", "c"}


def test_key_drops_excluded_even_when_in_a_requested_circle() -> None:
    assert "s" not in answer_key(["investors"], MEMBERS, BLOCKED)
    assert answer_key(["blocked"], MEMBERS, BLOCKED) == set()


def test_no_exclusion_configured_excludes_nobody() -> None:
    # A fresh install has no excluded circle; the key is then just the union.
    assert answer_key(["investors"], MEMBERS) == {"a", "b", "s"}


def test_key_unknown_circle_is_empty_not_an_error() -> None:
    assert answer_key(["no-such-circle"], MEMBERS, BLOCKED) == set()
    assert answer_key([], MEMBERS, BLOCKED) == set()


def test_hits_count_key_people_within_each_cutoff() -> None:
    ranked = ["z", "a", "y", "b", "c"]
    assert hits_at(ranked, {"a", "b", "c"}, ks=(1, 2, 4, 10)) == (0, 1, 2, 3)


def test_hits_on_empty_ranking() -> None:
    assert hits_at([], {"a"}) == (0, 0, 0)


def test_regression_reports_each_dropped_cutoff() -> None:
    out = regressions({"card": [8, 14, 28]}, {"card": [8, 15, 30]})
    assert out == ["card: @25 15->14, @50 30->28"]


def test_gains_new_arms_and_retired_arms_are_not_regressions() -> None:
    assert regressions({"card": [9, 16, 30], "new": [0, 0, 0]}, {"card": [8, 15, 28]}) == []
    assert regressions({}, {"old": [5, 5, 5]}) == []
