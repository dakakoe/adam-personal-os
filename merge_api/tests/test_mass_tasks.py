"""Mass-task bot capture: the 'tasks' card renders a per-item list + target
project (the bot echoes this verbatim, so the format matters)."""

from __future__ import annotations

from merge_api.app import _render_capture_summary


def test_tasks_card_lists_items_and_project():
    out = _render_capture_summary({
        "type": "tasks", "project_name": "Shopping List",
        "items": ["Apples", "Candies", "Screws"],
    })
    assert "3 tasks → Shopping List" in out
    for item in ("• Apples", "• Candies", "• Screws"):
        assert item in out


def test_tasks_card_defaults_project_and_singular():
    out = _render_capture_summary({"type": "tasks", "items": ["Milk"]})
    assert "1 task → Shopping List" in out
    assert "• Milk" in out


def test_tasks_card_truncates_long_lists():
    items = [f"item{i}" for i in range(25)]
    out = _render_capture_summary({"type": "tasks", "project_name": "Errands", "items": items})
    assert "25 tasks → Errands" in out
    assert "…and 5 more" in out
    assert "• item0" in out and "• item19" in out
    assert "• item20" not in out
