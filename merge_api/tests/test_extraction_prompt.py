"""Guards for the capture-extraction system prompt (CAPTURE_SYSTEM).

The LLM does the actual parsing, so we can't unit-test its output cheaply — but
we CAN pin the instructions that a real capture depended on. Regression context:
"Task: spreadsheet for Jane Doe, today at 12:00" extracted task_time=12:00 but
left due_date empty, because the TASK rule only resolved "Friday/tomorrow/next
week" and had no default-to-today when a time was given. The task then anchored to
no day and never reached the schedule. These assertions fail if that rule is lost."""

from __future__ import annotations

from merge_api import extraction


def test_task_due_date_resolves_today():
    # "today" must be an explicit relative date the model resolves.
    assert "today" in extraction.CAPTURE_SYSTEM.lower()


def test_task_time_with_no_date_defaults_to_today():
    # The actual bug: a task_time with no stated date must default due_date to today.
    assert "default due_date to TODAY" in extraction.CAPTURE_SYSTEM


def test_event_soonest_future_date_rule_still_present():
    # The sibling event rule that the task fix was modeled on — keep it too.
    assert "SOONEST future date" in extraction.CAPTURE_SYSTEM
