"""The greedy forward-fill that spreads the overdue backlog.

Pure arithmetic, unit-tested without a database because it's the part that
would quietly misplace forty follow-ups if the cursor logic were off by one.
The rules, from the user: start tomorrow, cap each day at 10 counting what's
already scheduled there, roll the overflow forward.
"""

from __future__ import annotations

from datetime import date

from merge_api.queries import plan_distribution

START = date(2026, 8, 24)  # a Monday; "tomorrow" in the tests


def _counts(plan: dict[str, str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for d in plan.values():
        out[d] = out.get(d, 0) + 1
    return out


def test_fills_one_day_to_the_cap_then_spills():
    ids = [f"f{i}" for i in range(12)]
    plan = plan_distribution(ids, {}, START, cap=10)
    c = _counts(plan)
    assert c["2026-08-24"] == 10
    assert c["2026-08-25"] == 2


def test_existing_load_is_counted():
    # 2026-08-24 already holds 8; only 2 overdue land there before spilling.
    ids = [f"f{i}" for i in range(5)]
    plan = plan_distribution(ids, {"2026-08-24": 8}, START, cap=10)
    c = _counts(plan)
    assert c["2026-08-24"] == 2
    assert c["2026-08-25"] == 3


def test_a_full_starting_day_is_skipped_entirely():
    plan = plan_distribution(["a", "b"], {"2026-08-24": 10}, START, cap=10)
    assert set(plan.values()) == {"2026-08-25"}


def test_priority_order_gets_the_earliest_slots():
    # ids arrive already most-overdue-first; the first id must land soonest.
    ids = ["oldest", "middle", "newest"]
    plan = plan_distribution(ids, {"2026-08-24": 9}, START, cap=10)
    assert plan["oldest"] == "2026-08-24"      # the last open slot on day one
    assert plan["middle"] == "2026-08-25"
    assert plan["newest"] == "2026-08-25"


def test_empty_backlog_is_empty_plan():
    assert plan_distribution([], {"2026-08-24": 3}, START) == {}


def test_cap_of_one_is_one_per_day():
    plan = plan_distribution(["a", "b", "c"], {}, START, cap=1)
    assert [plan["a"], plan["b"], plan["c"]] == ["2026-08-24", "2026-08-25", "2026-08-26"]


def test_zero_or_negative_cap_is_clamped_not_infinite():
    # A cap <= 0 would loop forever without the clamp; assert it terminates.
    plan = plan_distribution(["a", "b"], {}, START, cap=0)
    assert len(plan) == 2


class TestNextSlotViaPlanner:
    """next_followup_slot is the single-item case of the same greedy rule, so
    it's pinned here through plan_distribution: the one slot it returns is the
    first day under the cap, counting existing load. (The async DB wrapper is
    thin; this covers the arithmetic that decides the recommendation.)"""

    def _slot(self, load, cap=10):
        return plan_distribution(["x"], load, START, cap)["x"]

    def test_empty_days_recommend_tomorrow(self):
        assert self._slot({}) == "2026-08-24"

    def test_a_full_tomorrow_pushes_to_the_next_open_day(self):
        assert self._slot({"2026-08-24": 10}) == "2026-08-25"

    def test_a_day_with_room_is_recommended_even_if_partially_full(self):
        assert self._slot({"2026-08-24": 9}) == "2026-08-24"

    def test_several_full_days_are_all_skipped(self):
        load = {"2026-08-24": 10, "2026-08-25": 10, "2026-08-26": 10}
        assert self._slot(load) == "2026-08-27"
