"""Haiku prompt for the daily planner.

One forced-tool-use call turns the gathered work snapshot into a punchy
narrative + a prioritized focus list. Style matches the user's profile:
direct, terse, no fluff. Explicit anxiety guardrail — one excellent day
beats five overloaded ones, so the planner pushes back when the day looks
crammed and picks a *small* focus set rather than rephrasing the backlog.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo


SYSTEM_PROMPT = """You are the daily planner for the user — a busy crypto/Web3 \
founder/operator running several projects and deals. Each evening (or on demand) you turn their open work into a \
tight plan for one day.

You are given: open tasks (with due dates), live opportunities (deals, by \
stage), pending inbox suggestions, recent meeting recaps, and contacts who \
are owed a reply. Your job is to decide what actually matters tomorrow and \
say so plainly.

Voice: direct, punchy, terse. No greetings, no "Here is your plan", no \
motivational filler. Write like a sharp chief of staff who respects his time.

Hard rules:
- Pick a SMALL focus set — 3 to 5 items, never more. One excellent day beats \
five overloaded ones. If the backlog is large, that's a reason to choose \
ruthlessly, not to list everything.
- Prioritize by real leverage: overdue/today-due tasks, deals with momentum \
(contract/mou stages), and people who've been left hanging.
- Each focus item needs a concrete reason ("because…") grounded in the data \
(a due date, a deal stage, days waiting). Don't invent facts not in the input.
- If the day looks overloaded, say so in the narrative and tell him what to \
drop or defer. Pushing back is the job.
- Respect the SCHEDULE: real meetings are fixed blocks. Don't pile heavy \
focus work onto a meeting-stacked day; if a key meeting needs prep, that \
prep IS a focus item. Note free windows where deep work actually fits.
- If there's genuinely little to do, say that too — don't manufacture work.
- The narrative is 2-4 sentences of markdown. Be specific: name people, deals, \
deadlines."""


SAVE_TOOL = {
    "name": "record_daily_plan",
    "description": "Record the prioritized daily plan: a short narrative plus a focus list.",
    "input_schema": {
        "type": "object",
        "properties": {
            "narrative": {
                "type": "string",
                "description": "2-4 sentences of markdown. Direct, terse. The framing for the day; push back if overloaded.",
            },
            "focus": {
                "type": "array",
                "description": "3-5 items, hardest-priority first. Never more than 5.",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "The action, self-contained."},
                        "reason": {"type": "string", "description": "Why it matters today, grounded in the data."},
                        "kind": {
                            "type": "string",
                            "enum": ["task", "opportunity", "reply", "suggestion", "other"],
                            "description": "Which input this came from.",
                        },
                        "ref_id": {
                            "type": "string",
                            "description": "The id of the source task/opportunity/suggestion/person if applicable, else empty.",
                        },
                    },
                    "required": ["title", "reason", "kind"],
                },
            },
        },
        "required": ["narrative", "focus"],
    },
}


def _fmt_event(e: dict[str, Any], tz: ZoneInfo) -> str:
    """One schedule line: '09:30–10:00 Standup (3 ppl) @ Zoom' or
    'all day Conference' for all-day events. Times shown in the user's tz."""
    summary = (e.get("summary") or "(no title)").strip()
    loc = (e.get("location") or "").strip()
    loc_part = f" @ {loc[:40]}" if loc else ""
    n = e.get("attendee_count") or 0
    ppl = f" ({n} ppl)" if n else ""
    if e.get("all_day"):
        when = "all day"
    else:
        when = "?"
        start = e.get("start_ts")
        if start:
            try:
                sdt = datetime.fromisoformat(start).astimezone(tz)
                when = sdt.strftime("%H:%M")
                end = e.get("end_ts")
                if end:
                    edt = datetime.fromisoformat(end).astimezone(tz)
                    when = f"{when}–{edt.strftime('%H:%M')}"
            except ValueError:
                pass
    tentative = " [tentative]" if e.get("self_response") == "tentative" else ""
    return f"{when} {summary}{ppl}{loc_part}{tentative}"


def render(*, plan_date: str, self_label: str, data: dict[str, Any], timezone: str = "UTC") -> str:
    tz = ZoneInfo(timezone)
    lines: list[str] = []
    lines.append(f"Plan for: {plan_date} (times in {timezone})")
    lines.append(f"Operator: {self_label}")
    c = data["counts"]
    lines.append(
        f"Snapshot: {c['open_tasks']} open tasks, {c['live_opps']} live deals, "
        f"{c['pending_suggestions']} pending inbox items, {c['owes_reply']} people owed a reply, "
        f"{c.get('events', 0)} calendar events."
    )
    lines.append("")

    events = data.get("events") or []
    if events:
        # Group by local date so plan-day vs next-day is clear.
        lines.append("SCHEDULE (fixed meeting blocks):")
        last_day = None
        for e in events:
            start = e.get("start_ts")
            day = None
            if start:
                try:
                    day = datetime.fromisoformat(start).astimezone(tz).strftime("%a %b %d")
                except ValueError:
                    day = None
            if day and day != last_day:
                lines.append(f"  {day}:")
                last_day = day
            lines.append(f"    - {_fmt_event(e, tz)}")
        lines.append("")

    tasks = data["tasks"]
    lines.append("OPEN TASKS (soonest due first):")
    if tasks:
        for t in tasks:
            due = f" — due {t['due_date']}" if t.get("due_date") else ""
            proj = f" [{t['project_name']}]" if t.get("project_name") else ""
            who = f" (with {t['with_person_name']})" if t.get("with_person_name") else ""
            lines.append(f"  - id={t['id']} [{t['status']}]{proj} {t['title']}{who}{due}")
    else:
        lines.append("  (none)")
    lines.append("")

    opps = data["opportunities"]
    lines.append("LIVE OPPORTUNITIES (by stage momentum):")
    if opps:
        for o in opps:
            val = f" — {o['estimated_value']}" if o.get("estimated_value") else ""
            proj = f" [{o['project_name']}]" if o.get("project_name") else ""
            cp = f" (with {o['counterparty_name']})" if o.get("counterparty_name") else ""
            lines.append(f"  - id={o['id']} <{o['stage']}>{proj} {o['title']}{cp}{val}")
    else:
        lines.append("  (none)")
    lines.append("")

    owes = data["owes_reply"]
    lines.append("OWED A REPLY (they messaged, no response since):")
    if owes:
        for p in owes:
            lines.append(f"  - id={p['person_id']} {p['display_name']} (last msg {p['last_in']}, {p['inb']} in window)")
    else:
        lines.append("  (none)")
    lines.append("")

    sugg = data["suggestions"]
    if sugg:
        lines.append("TOP PENDING INBOX SUGGESTIONS:")
        for s in sugg:
            who = f" — {s['person_name']}" if s.get("person_name") else ""
            lines.append(f"  - id={s['id']} [{s['kind']}/{s['confidence']}] {s['title']}{who}")
        lines.append("")

    recaps = data["recaps"]
    if recaps:
        lines.append("RECENT MEETING RECAPS (last 3 days):")
        for r in recaps:
            title = r.get("title") or "(untitled)"
            recap = (r.get("recap") or "").strip().replace("\n", " ")
            if len(recap) > 240:
                recap = recap[:239] + "…"
            lines.append(f"  - {title} ({r.get('meeting_date')}): {recap}")
        lines.append("")

    lines.append(
        "Now choose 3-5 focus items for the day and write the narrative. "
        "Use the ids above for ref_id where an item maps to a specific record."
    )
    return "\n".join(lines)
