"""The greedy forward-fill that spreads the overdue backlog.

Pure arithmetic, unit-tested without a database because it's the part that
would quietly misplace forty follow-ups if the cursor logic were off by one.
The rules, from the user: start tomorrow, cap each day at 10 counting what's
already scheduled there, roll the overflow forward.
"""

from __future__ import annotations

from datetime import date, time

from merge_api.queries import context_line, distribution_order, plan_distribution

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
    # ids arrive already in slot order; the first id must land soonest.
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


class TestFloors:
    """`not_before` is what keeps distribute a backlog spreader rather than a
    scheduler: a follow-up already dated ahead can be pushed later to make room
    for something that matters more, but never dragged into tomorrow."""

    def test_a_floor_is_respected_even_when_earlier_days_are_empty(self):
        plan = plan_distribution(["late"], {}, START, cap=10,
                                 not_before={"late": date(2026, 9, 1)})
        assert plan["late"] == "2026-09-01"

    def test_a_floored_item_still_spills_when_its_own_day_is_full(self):
        plan = plan_distribution(["a"], {"2026-09-01": 10}, START, cap=10,
                                 not_before={"a": date(2026, 9, 1)})
        assert plan["a"] == "2026-09-02"

    def test_ids_without_a_floor_start_at_the_start_date(self):
        plan = plan_distribution(["free", "pinned"], {}, START, cap=10,
                                 not_before={"pinned": date(2026, 8, 30)})
        assert plan["free"] == "2026-08-24"
        assert plan["pinned"] == "2026-08-30"

    def test_a_high_priority_overdue_pushes_a_full_day_of_lower_ones_back(self):
        # The whole point of priority: tomorrow is full of low-priority items
        # already sitting there, and the one that matters still gets tomorrow.
        ids = ["high"] + [f"low{i}" for i in range(10)]
        floors = {i: START for i in ids}          # the lows are already on day one
        plan = plan_distribution(ids, {}, START, cap=10, not_before=floors)
        assert plan["high"] == "2026-08-24"
        assert plan["low9"] == "2026-08-25"       # exactly one is bumped
        assert sum(1 for d in plan.values() if d == "2026-08-24") == 10


TODAY = date(2026, 8, 23)


def _row(id, due, priority="mid", due_time=None):
    return {"id": id, "due_date": due, "due_time": due_time, "priority": priority}


class TestDistributionOrder:
    """What may move, and who gets the early days."""

    def test_high_goes_before_mid_before_low_regardless_of_lateness(self):
        rows = [
            _row("low-ancient", date(2026, 1, 1), "low"),
            _row("mid-old", date(2026, 7, 1), "mid"),
            _row("high-recent", date(2026, 8, 22), "high"),
        ]
        assert [r["id"] for r in distribution_order(rows, TODAY)] == [
            "high-recent", "mid-old", "low-ancient"]

    def test_within_a_priority_the_most_overdue_leads(self):
        rows = [_row("newer", date(2026, 8, 1)), _row("older", date(2026, 2, 1))]
        assert [r["id"] for r in distribution_order(rows, TODAY)] == ["older", "newer"]

    def test_today_is_left_alone(self):
        rows = [_row("today", TODAY, "high"), _row("overdue", date(2026, 8, 1))]
        assert [r["id"] for r in distribution_order(rows, TODAY)] == ["overdue"]

    def test_a_timed_followup_is_never_moved(self):
        rows = [_row("3pm-thursday", date(2026, 8, 1), "high", time(15, 0))]
        assert distribution_order(rows, TODAY) == []

    def test_dateless_followups_stay_in_the_someday_pile(self):
        assert distribution_order([_row("someday", None)], TODAY) == []

    def test_future_all_day_rows_are_movable_so_they_can_be_pushed_back(self):
        rows = [_row("next-month", date(2026, 9, 20), "low")]
        assert [r["id"] for r in distribution_order(rows, TODAY)] == ["next-month"]


class TestContextLine:
    """The one line of "who is this" a follow-up row shows under the name."""

    def test_the_card_wins_and_the_name_is_not_repeated(self):
        assert context_line("Ada Lovelace. Head of BD at a fund.", "ignored", "Ada Lovelace") \
            == "Head of BD at a fund."

    def test_the_summary_is_the_fallback_and_only_its_first_sentence(self):
        assert context_line(None, "Runs a market maker. You met in Seoul.", "Kim") \
            == "Runs a market maker."

    def test_nothing_to_say_is_none_not_an_empty_string(self):
        assert context_line(None, None, "Nobody") is None
        assert context_line("Nobody.", None, "Nobody") is None

    def test_a_long_card_is_cut_on_a_word_boundary(self):
        line = context_line("X. " + "word " * 100, None, "X")
        assert line.endswith("…") and len(line) <= 201 and " wor…" not in line
