"""The brief's shaping rules, and how merge_api errors reach the model."""
from __future__ import annotations

import asyncio
from datetime import date

import httpx

from server import assistant

TODAY = date(2026, 10, 18)


def test_due_buckets_split_overdue_today_doing_and_count_undated() -> None:
    tasks = [
        {"id": "a", "title": "late", "status": "open", "due_date": "2026-10-10"},
        {"id": "b", "title": "now", "status": "open", "due_date": "2026-10-18", "due_time": "14:00:00"},
        {"id": "c", "title": "early", "status": "open", "due_date": "2026-10-18", "due_time": "09:00:00"},
        {"id": "d", "title": "later", "status": "open", "due_date": "2026-11-01"},
        {"id": "e", "title": "someday", "status": "open"},
        {"id": "f", "title": "in progress", "status": "doing"},
        {"id": "g", "title": "done", "status": "done", "due_date": "2026-10-01"},
    ]
    b = assistant.due_buckets(tasks, TODAY)
    assert [t["id"] for t in b["overdue"]] == ["a"]
    assert [t["id"] for t in b["today"]] == ["c", "b"]        # by time
    assert [t["id"] for t in b["doing"]] == ["f"]
    assert b["undated_open"] == 1                              # e, not g or d


def test_due_followups_keeps_only_owed_ones_high_first() -> None:
    rows = [
        {"id": "1", "due_date": "2026-10-20", "priority": "high", "status": "open"},
        {"id": "2", "due_date": "2026-10-17", "priority": "low", "status": "open"},
        {"id": "3", "due_date": None, "priority": "high", "status": "open"},
        {"id": "4", "due_date": "2026-10-01", "priority": "mid", "status": "connected"},
    ]
    assert [f["id"] for f in assistant.due_followups(rows, TODAY)] == ["3", "2"]


def test_deals_by_stage_drops_closed_and_lost() -> None:
    rows = [
        {"id": "1", "stage": "intro", "updated_at": "2026-10-01"},
        {"id": "2", "stage": "mou", "updated_at": "2026-10-15"},
        {"id": "3", "stage": "lost", "updated_at": "2026-10-16"},
        {"id": "4", "stage": "contract", "closed_at": "2026-09-01", "updated_at": "2026-10-17"},
    ]
    d = assistant.deals_by_stage(rows)
    assert d["live"] == 2
    assert d["by_stage"] == {"intro": 1, "mou": 1}
    assert [x["id"] for x in d["recent"]] == ["2", "1"]       # most recently moved first


def test_events_list_accepts_both_shapes() -> None:
    assert assistant.events_list({"events": [{"summary": "x"}], "timezone": "UTC"}) == [{"summary": "x"}]
    assert assistant.events_list([{"summary": "y"}]) == [{"summary": "y"}]
    assert assistant.events_list(None) == []


def test_compact_event_reads_the_api_names() -> None:
    e = assistant.compact_event({"summary": "Standup", "start_ts": "2026-10-18T09:00:00+07:00",
                                 "end_ts": "2026-10-18T09:15:00+07:00", "all_day": False,
                                 "attendee_count": 4, "self_response": "accepted",
                                 "account_email": "me@example.com"})
    assert e["title"] == "Standup" and e["start"].startswith("2026-10-18T09") and e["attendees"] == 4


def test_plan_summary_lifts_focus_out_of_structured() -> None:
    p = {"plan_date": "2026-10-18", "narrative": "n", "generated_at": "t",
         "structured": {"focus": [{"title": "a"}], "tasks": ["noise"]}}
    assert assistant.plan_summary(p) == {"for": "2026-10-18", "generated_at": "t",
                                         "narrative": "n", "focus": [{"title": "a"}]}
    assert assistant.plan_summary(None) is None


def _api(handler) -> assistant.MergeAPI:
    return assistant.MergeAPI("http://api.test", "tok", transport=httpx.MockTransport(handler))


def test_merge_api_sends_the_admin_token_and_drops_none_params() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["url"] = str(request.url)
        return httpx.Response(200, json=[{"id": "1"}])

    out = asyncio.run(_api(handler).get("/api/tasks", status="open", q=None, limit=5))
    assert out == [{"id": "1"}]
    assert seen["auth"] == "Bearer tok"
    assert "q=" not in seen["url"] and "status=open" in seen["url"]


def test_merge_api_errors_carry_the_detail() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"detail": "this contact has no Telegram identity to send to"})

    try:
        asyncio.run(_api(handler).post("/api/drafts/x/send"))
    except assistant.ToolError as e:
        assert "no Telegram identity" in str(e) and "400" in str(e)
    else:
        raise AssertionError("expected ToolError")


def test_guide_exists_and_states_the_send_rule() -> None:
    text = assistant.GUIDE_PATH.read_text(encoding="utf-8")
    assert "send_draft" in text and "said yes" in text


def test_require_id_explains_instead_of_a_500() -> None:
    assert assistant.require_id("task_id", " 74e67611-e0c8-46a6-81de-93b04be70929 ") == "74e67611-e0c8-46a6-81de-93b04be70929"
    for bad in ("", "not-a-uuid", "74e67611e0c846a681de93b04be70929"):
        try:
            assistant.require_id("task_id", bad)
        except assistant.ToolError as e:
            assert "task_id" in str(e) and "list tools" in str(e)
        else:
            raise AssertionError(bad)


def test_compact_note_never_leaves_an_empty_title() -> None:
    n = assistant.compact_note({"id": "n1", "title": "", "snippet": "x", "pinned": None})
    assert n["title"] == "(untitled)" and n["pinned"] is False and n["snippet"] == "x"



def test_sensitive_people_are_hidden_or_redacted() -> None:
    hidden = frozenset({"p-secret"})
    rows = [{"id": "1", "person_id": "p-secret"}, {"id": "2", "person_id": "p-ok"}]
    assert [r["id"] for r in assistant.hide_sensitive(rows, hidden, "person_id")] == ["2"]
    tasks = [{"id": "t1", "title": "Call", "with": "Secret Person", "with_person_id": "p-secret"},
             {"id": "t2", "title": "Mail", "with": "Open Person", "with_person_id": "p-ok"}]
    out = assistant.redact_with(tasks, hidden)
    assert out[0]["with"] == assistant.sens.NOTE and out[0]["with_person_id"] is None
    assert out[0]["title"] == "Call" and out[1]["with"] == "Open Person"
    try:
        assistant.require_not_sensitive("p-secret", hidden)
    except assistant.ToolError as e:
        assert "sensitive" in str(e)
    else:
        raise AssertionError("expected refusal")
    assert assistant.hide_sensitive(rows, frozenset(), "person_id") == rows
