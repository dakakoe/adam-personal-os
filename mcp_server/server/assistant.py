"""Assistant tools: the owner's day as the connector sees and changes it.

Every read and write goes through merge_api over HTTP with the admin token,
so the rules the app enforces (who can be messaged, which stages exist, the
local-model routing for sensitive contacts) hold here too; nothing touches
the tables directly. The one exception is notify_me, a single Telegram Bot
API call to the owner's own chat.

What counts as due, how a deal list is summarised and how rows are trimmed
are module-level functions so tests cover them without a server.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from . import db, sensitive as sens
from .config import Config

log = logging.getLogger(__name__)

GUIDE_PATH = Path(__file__).with_name("templates") / "assistant_guide.md"

READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False,
                        open_world_hint=False)
# Leaves the system: a message to a contact, or to the owner's phone.
SEND = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False,
                       open_world_hint=True)

CLOSED_STAGES = {"lost"}
MAX_NOTIFY_CHARS = 3500
MAX_BRIEF_FOLLOWUPS = 30
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


class ToolError(ValueError):
    """A request the tool can explain but not fulfil."""


def require_id(name: str, value: str) -> str:
    """Ids are the uuids the list tools return. Catching a bad one here gives
    the model a sentence instead of merge_api's 500 on the uuid cast."""
    value = (value or "").strip()
    if not UUID_RE.match(value):
        raise ToolError(f"{name} must be an id from the list tools (a uuid), got {value!r}")
    return value


# --- merge_api client ----------------------------------------------------

def _clean(params: dict[str, Any] | None) -> dict[str, Any] | None:
    if not params:
        return None
    return {k: v for k, v in params.items() if v is not None}


def _detail(r: httpx.Response) -> str:
    try:
        body = r.json()
        if isinstance(body, dict) and body.get("detail"):
            return str(body["detail"])
    except ValueError:
        pass
    return r.text[:300]


class MergeAPI:
    """The admin-token HTTP client. Errors come back as ToolError carrying
    the API's own `detail`, which is what the calling model should read."""

    def __init__(self, base: str, token: str, transport: httpx.AsyncBaseTransport | None = None):
        self._client = httpx.AsyncClient(
            base_url=base, headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(30.0, read=150.0), transport=transport)

    async def call(self, method: str, path: str, *, params: dict | None = None,
                   json: Any = None) -> Any:
        try:
            r = await self._client.request(method, path, params=_clean(params), json=json)
        except httpx.HTTPError as e:
            raise ToolError(f"merge_api unreachable: {e}") from e
        if r.status_code >= 400:
            raise ToolError(f"{method} {path} failed ({r.status_code}): {_detail(r)}")
        if r.status_code == 204 or not r.content:
            return None
        return r.json()

    async def get(self, path: str, **params: Any) -> Any:
        return await self.call("GET", path, params=params)

    async def post(self, path: str, json: Any = None) -> Any:
        return await self.call("POST", path, json=json)

    async def patch(self, path: str, json: Any) -> Any:
        return await self.call("PATCH", path, json=json)


# --- the sensitive rule, applied to merge_api rows ---------------------------
# merge_api rows name people without saying whether they are sensitive; the
# server knows (canonical.person.sensitive). Read the set at most once a minute
# and hide those people from everything the cloud model sees, as tools.py does.

_sensitive_cache: dict[str, Any] = {"ids": frozenset(), "at": 0.0}


async def sensitive_ids() -> frozenset[str]:
    import time
    now = time.monotonic()
    if now - _sensitive_cache["at"] < 60:
        return _sensitive_cache["ids"]
    try:
        pool = await db.pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT id::text FROM canonical.person WHERE sensitive")
        ids = frozenset(r["id"] for r in rows)
    except Exception:  # noqa: BLE001 — fail closed would hide everything; log and keep the last set
        log.warning("could not read sensitive contacts; keeping the last known set", exc_info=True)
        ids = _sensitive_cache["ids"]
    _sensitive_cache.update(ids=ids, at=now)
    return ids


def hide_sensitive(rows: list[dict], ids: frozenset[str], *keys: str) -> list[dict]:
    """Drop rows that belong to a sensitive person (by any of `keys`)."""
    if not ids:
        return rows
    return [r for r in rows if not any(r.get(k) in ids for k in keys)]


def redact_with(rows: list[dict], ids: frozenset[str]) -> list[dict]:
    """A task linked to a sensitive person keeps its title but loses the name."""
    if not ids:
        return rows
    out = []
    for r in rows:
        if r.get("with_person_id") in ids:
            r = {**r, "with": sens.NOTE, "with_person_id": None}
        out.append(r)
    return out


def require_not_sensitive(person_id: str, ids: frozenset[str]) -> None:
    if person_id in ids:
        raise ToolError(sens.NOTE + " — do this in the app")


# --- shaping ---------------------------------------------------------------

def _d(v: Any) -> date | None:
    """A date from an ISO date/datetime string, or None."""
    if not v:
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def compact_task(t: dict) -> dict:
    return {
        "id": t.get("id"), "title": t.get("title"), "status": t.get("status"),
        "due_date": t.get("due_date"), "due_time": t.get("due_time"),
        "project": t.get("project_name"), "deal": t.get("opportunity_title"),
        "with": t.get("with_person_name"), "with_person_id": t.get("with_person_id"),
    }


def due_buckets(tasks: list[dict], today: date) -> dict[str, list[dict]]:
    """Open work that needs a decision today: overdue, due today, and what is
    already in progress. Undated open tasks are counted, not listed — the
    backlog is for /plan, not the morning."""
    overdue, due_today, doing, undated = [], [], [], 0
    for t in tasks:
        if t.get("status") == "doing":
            doing.append(compact_task(t))
            continue
        if t.get("status") != "open":
            continue
        d = _d(t.get("due_date"))
        if d is None:
            undated += 1
        elif d < today:
            overdue.append(compact_task(t))
        elif d == today:
            due_today.append(compact_task(t))
    overdue.sort(key=lambda x: (x["due_date"] or "", x["due_time"] or ""))
    due_today.sort(key=lambda x: (x["due_time"] or "99:99"))
    return {"overdue": overdue, "today": due_today, "doing": doing, "undated_open": undated}


def compact_followup(f: dict) -> dict:
    return {
        "id": f.get("id"), "person_id": f.get("person_id"), "name": f.get("display_name"),
        "due_date": f.get("due_date"), "due_time": f.get("due_time"),
        "topic": f.get("topic"), "priority": f.get("priority"), "company": f.get("company"),
        "last_interaction_at": f.get("last_interaction_at"),
    }


def due_followups(rows: list[dict], today: date) -> list[dict]:
    out = [compact_followup(f) for f in rows
           if f.get("status", "open") == "open" and (_d(f.get("due_date")) or today) <= today]
    prio = {"high": 0, "mid": 1, "low": 2}
    out.sort(key=lambda x: (prio.get(x["priority"] or "mid", 1), x["due_date"] or ""))
    return out


def compact_event(e: dict) -> dict:
    return {
        "title": e.get("summary") or e.get("title"),
        "start": e.get("start_ts") or e.get("start"), "end": e.get("end_ts") or e.get("end"),
        "all_day": bool(e.get("all_day")), "location": e.get("location"),
        "attendees": e.get("attendee_count"), "my_response": e.get("self_response"),
        "calendar": e.get("account_email"),
    }


def events_list(payload: Any) -> list[dict]:
    """/api/events answers {"events": [...], "timezone": ...}; accept a bare
    list too so a shape change does not blank the schedule."""
    if isinstance(payload, dict):
        return list(payload.get("events") or [])
    return list(payload or [])


def compact_deal(o: dict) -> dict:
    return {
        "id": o.get("id"), "title": o.get("title"), "stage": o.get("stage"),
        "counterparty": o.get("counterparty_name"),
        "company": o.get("company_name") or o.get("company"),
        "tags": o.get("tags") or [], "value": o.get("estimated_value"),
        "award_usd": o.get("award_usd"), "open_tasks": o.get("open_task_count"),
        "updated_at": o.get("updated_at"),
    }


def deals_by_stage(rows: list[dict], limit: int = 30) -> dict:
    """Live deals only (not lost, not closed), newest movement first, plus a
    count per stage so the model sees the shape of the pipeline."""
    live = [o for o in rows if o.get("stage") not in CLOSED_STAGES and not o.get("closed_at")]
    by_stage: dict[str, int] = {}
    for o in live:
        by_stage[o.get("stage") or "?"] = by_stage.get(o.get("stage") or "?", 0) + 1
    live.sort(key=lambda o: o.get("updated_at") or "", reverse=True)
    return {"live": len(live), "by_stage": by_stage,
            "recent": [compact_deal(o) for o in live[:limit]]}


def compact_circle_due(c: dict) -> dict:
    return {
        "person_id": c.get("person_id"), "name": c.get("display_name"),
        "circle": c.get("circle_label"), "days_since": c.get("days_since"),
        "days_overdue": c.get("days_overdue"), "cadence_days": c.get("cadence_days"),
    }


def compact_note(n: dict) -> dict:
    return {
        "id": n.get("id"), "title": n.get("title") or "(untitled)", "parent_id": n.get("parent_id"),
        "pinned": bool(n.get("pinned")), "snippet": n.get("snippet"),
        "person_id": n.get("person_id"), "opportunity_id": n.get("opportunity_id"),
        "updated_at": n.get("updated_at"),
    }


def plan_summary(plan: dict | None) -> dict | None:
    if not plan:
        return None
    structured = plan.get("structured") or {}
    return {
        "for": plan.get("plan_date"), "generated_at": plan.get("generated_at"),
        "narrative": plan.get("narrative"), "focus": structured.get("focus") or [],
    }


# --- registration ----------------------------------------------------------

def register(mcp: FastMCP, cfg: Config, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
    """Add the assistant tools. Needs the merge_api admin token; without it
    the server stays read-only and says so once at startup."""
    if not cfg.merge_api_token:
        log.warning("MERGE_API_BEARER_TOKEN unset — assistant tools (tasks, follow-ups, "
                    "deals, drafts, capture, brief) are not registered")
        return
    api = MergeAPI(cfg.merge_api_base, cfg.merge_api_token, transport=transport)
    tz = ZoneInfo(cfg.timezone)

    def today() -> date:
        return datetime.now(tz).date()

    @mcp.tool(annotations=READ)
    async def adam_guide() -> str:
        """How to work with ADAM as the owner's assistant: the morning
        routine, what each tool is for, and what needs the owner's explicit
        yes. Read this once at the start of a session."""
        return GUIDE_PATH.read_text(encoding="utf-8")

    @mcp.tool(annotations=READ)
    async def morning_brief(regenerate: bool = False) -> dict[str, Any]:
        """Everything for planning the day in one call: last night's plan
        (narrative + focus), live counts, today's calendar, overdue and
        due-today tasks, follow-ups owed, contacts past their circle
        cadence, the live deal pipeline, and pending inbox suggestions.
        `regenerate=true` rewrites today's plan first (a model call, up to
        2 minutes) — use it when the day has changed, not by default."""
        if regenerate:
            await api.post("/api/today/regenerate")
        day = today()
        (plan, counts, events, open_tasks, doing, followups, circles, deals,
         suggestions) = await asyncio.gather(
            api.get("/api/today"),
            api.get("/api/today/counts"),
            api.get("/api/events", start=day.isoformat()),
            api.get("/api/tasks", status="open", limit=200),
            api.get("/api/tasks", status="doing", limit=200),
            api.get("/api/followups", scope="open", limit=200),
            api.get("/api/circles/due", limit=20),
            api.get("/api/opportunities", limit=200),
            api.get("/api/suggestions/count"),
        )
        hidden = await sensitive_ids()
        owed = hide_sensitive(due_followups(list(followups or []), day), hidden, "person_id")
        buckets = due_buckets(hide_sensitive(list(open_tasks or []) + list(doing or []), hidden, "assignee_person_id"), day)
        buckets = {k: (redact_with(v, hidden) if isinstance(v, list) else v) for k, v in buckets.items()}
        return {
            "date": day.isoformat(), "timezone": cfg.timezone,
            "plan": plan_summary(plan),
            "counts": counts,
            "schedule": [compact_event(e) for e in events_list(events)],
            "tasks": buckets,
            "followups_due": owed[:MAX_BRIEF_FOLLOWUPS],
            "followups_due_total": len(owed),
            "circles_due": hide_sensitive([compact_circle_due(c) for c in (circles or [])], hidden, "person_id"),
            "deals": deals_by_stage(list(deals or [])),
            "inbox_suggestions_pending": (suggestions or {}).get("pending"),
        }

    # --- tasks ---

    @mcp.tool(annotations=READ)
    async def list_tasks(status: str = "open", q: str | None = None,
                         project_id: str | None = None, with_person_id: str | None = None,
                         limit: int = 50) -> list[dict[str, Any]]:
        """Tasks by status (open | doing | done | cancelled), optional text
        search and project/person filter. Newest first."""
        hidden = await sensitive_ids()
        if with_person_id:
            require_not_sensitive(with_person_id, hidden)
        rows = await api.get("/api/tasks", status=status, q=q, project_id=project_id,
                             with_person_id=with_person_id, limit=max(1, min(limit, 200)))
        return redact_with([compact_task(t) for t in rows], hidden)

    @mcp.tool(annotations=WRITE)
    async def create_task(title: str, due_date: str | None = None, due_time: str | None = None,
                          description: str | None = None, project_id: str | None = None,
                          with_person_id: str | None = None, opportunity_id: str | None = None,
                          status: str = "open") -> dict[str, Any]:
        """Create a task. Dates are ISO (2026-10-20), times HH:MM, in the
        owner's timezone. Link it to a deal with opportunity_id so it shows
        on the deal; to a person with with_person_id."""
        if with_person_id:
            require_not_sensitive(with_person_id, await sensitive_ids())
        body = _clean({"title": title, "due_date": due_date, "due_time": due_time,
                       "description": description, "project_id": project_id,
                       "with_person_id": with_person_id, "opportunity_id": opportunity_id,
                       "status": status})
        return compact_task(await api.post("/api/tasks", json=body))

    @mcp.tool(annotations=WRITE)
    async def update_task(task_id: str, title: str | None = None, status: str | None = None,
                          due_date: str | None = None, due_time: str | None = None,
                          description: str | None = None,
                          project_id: str | None = None) -> dict[str, Any]:
        """Change a task's title, status (open | doing | done | cancelled),
        due date/time, description or project. Only the fields given change."""
        body = _clean({"title": title, "status": status, "due_date": due_date,
                       "due_time": due_time, "description": description,
                       "project_id": project_id})
        if not body:
            raise ToolError("nothing to change")
        task_id = require_id("task_id", task_id)
        return compact_task(await api.patch(f"/api/tasks/{task_id}", json=body))

    @mcp.tool(annotations=WRITE)
    async def complete_task(task_id: str) -> dict[str, Any]:
        """Mark a task done."""
        task_id = require_id("task_id", task_id)
        return compact_task(await api.patch(f"/api/tasks/{task_id}", json={"status": "done"}))

    # --- follow-ups ---

    @mcp.tool(annotations=READ)
    async def list_followups(scope: str = "open", person_id: str | None = None) -> list[dict[str, Any]]:
        """People the owner means to get back to. scope 'open' (still owed)
        or 'all'. A follow-up settles itself when a real conversation
        happens; settle_followup is for a call or a coffee the system
        cannot see."""
        hidden = await sensitive_ids()
        if person_id:
            require_not_sensitive(person_id, hidden)
        rows = await api.get("/api/followups", scope=scope, person_id=person_id, limit=200)
        return hide_sensitive([compact_followup(f) for f in rows], hidden, "person_id")

    @mcp.tool(annotations=WRITE)
    async def create_followup(person_id: str, due_date: str | None = None,
                              due_time: str | None = None, topic: str | None = None,
                              priority: str = "mid") -> dict[str, Any]:
        """Promise to get back to someone: who (person_id from search_people
        or find_people), by when, about what. priority low | mid | high."""
        person_id = require_id("person_id", person_id)
        require_not_sensitive(person_id, await sensitive_ids())
        body = _clean({"person_id": person_id, "due_date": due_date, "due_time": due_time,
                       "topic": topic, "priority": priority})
        return compact_followup(await api.post("/api/followups", json=body))

    @mcp.tool(annotations=WRITE)
    async def settle_followup(followup_id: str, connected_via: str = "call") -> dict[str, Any]:
        """Mark a follow-up done by hand (connected_via: call, meeting,
        telegram, email, …)."""
        followup_id = require_id("followup_id", followup_id)
        row = await api.patch(f"/api/followups/{followup_id}",
                              json={"connected": True, "connected_via": connected_via})
        return compact_followup(row)

    # --- deals ---

    @mcp.tool(annotations=READ)
    async def list_opportunities(stage: str | None = None, tags: str | None = None,
                                 q: str | None = None, include_closed: bool = False,
                                 limit: int = 50) -> dict[str, Any]:
        """The deal pipeline. tags is comma-separated (a deal matches any of
        them). Returns per-stage counts and the deals, most recently moved
        first; closed/lost deals only with include_closed."""
        rows = await api.get("/api/opportunities", stage=stage, tags=tags, q=q,
                             limit=max(1, min(limit, 200)))
        if include_closed:
            return {"deals": [compact_deal(o) for o in rows]}
        return deals_by_stage(rows, limit=limit)

    @mcp.tool(annotations=WRITE)
    async def move_stage(opportunity_id: str, stage: str, next_step: str | None = None,
                         note: str | None = None) -> dict[str, Any]:
        """Move a deal to a stage (the keys from list_opportunities' by_stage,
        or /api/stages). Say what the next step is."""
        opportunity_id = require_id("opportunity_id", opportunity_id)
        body = _clean({"stage": stage, "next_step": next_step, "note": note})
        return compact_deal(await api.post(f"/api/opportunities/{opportunity_id}/stage", json=body))

    @mcp.tool(annotations=WRITE)
    async def log_deal_event(opportunity_id: str, note: str,
                             next_step: str | None = None) -> dict[str, Any]:
        """Add a note (and optionally the next step) to a deal's timeline
        without changing its stage."""
        opportunity_id = require_id("opportunity_id", opportunity_id)
        return await api.post(f"/api/opportunities/{opportunity_id}/events",
                              json=_clean({"note": note, "next_step": next_step}))

    @mcp.tool(annotations=READ)
    async def list_projects() -> list[dict[str, Any]]:
        """Projects (id, slug, name) — for project_id on tasks."""
        rows = await api.get("/api/projects")
        return [{"id": p.get("id"), "slug": p.get("slug"), "name": p.get("name"),
                 "status": p.get("status")} for p in rows]

    # --- messages ---

    @mcp.tool(annotations=WRITE)
    async def draft_message(person_id: str, body: str, channel: str = "telegram",
                            subject: str | None = None, task_id: str | None = None,
                            opportunity_id: str | None = None) -> dict[str, Any]:
        """Save a message draft to a contact (channel telegram | email;
        subject for email). Nothing is sent: the owner reviews it in the app
        or approves it here, then send_draft goes out FROM the owner's own
        account. Write in the owner's voice, short, no filler."""
        person_id = require_id("person_id", person_id)
        require_not_sensitive(person_id, await sensitive_ids())
        return await api.post(f"/api/persons/{person_id}/drafts", json=_clean({
            "channel": channel, "body": body, "subject": subject,
            "task_id": task_id, "opportunity_id": opportunity_id, "author": "assistant"}))

    @mcp.tool(annotations=READ)
    async def list_drafts(person_id: str) -> list[dict[str, Any]]:
        """Drafts waiting for a contact, newest first."""
        person_id = require_id("person_id", person_id)
        require_not_sensitive(person_id, await sensitive_ids())
        return await api.get(f"/api/persons/{person_id}/drafts")

    @mcp.tool(annotations=SEND)
    async def send_draft(draft_id: str) -> dict[str, Any]:
        """Send a saved draft for real, from the owner's account (Telegram via
        their own session, email via Gmail). Only after the owner has seen
        this exact draft and said yes to sending it — never on your own
        initiative, never in a batch."""
        draft_id = require_id("draft_id", draft_id)
        return await api.post(f"/api/drafts/{draft_id}/send")

    # --- capture ---

    @mcp.tool(annotations=WRITE)
    async def capture(text: str) -> dict[str, Any]:
        """Hand a natural-language note to the same classifier the Telegram
        bot uses: it becomes a pending task, deal, calendar event (with
        attendees + invites) or expense, with the people and dates it
        recognised. Nothing exists until confirm_capture. Use it for
        anything with a date, a person or a place; use create_task for a
        plain task."""
        return await api.post("/api/capture", json={"text": text, "source": "assistant"})

    @mcp.tool(annotations=WRITE)
    async def confirm_capture(capture_id: str, override_type: str | None = None) -> dict[str, Any]:
        """Create what a capture proposed. override_type (task | opportunity |
        event | …) if the classifier picked wrong."""
        capture_id = require_id("capture_id", capture_id)
        return await api.post(f"/api/capture/{capture_id}/confirm",
                              json=_clean({"override_type": override_type}) or {})

    @mcp.tool(annotations=WRITE)
    async def discard_capture(capture_id: str) -> dict[str, Any]:
        """Drop a pending capture."""
        capture_id = require_id("capture_id", capture_id)
        return await api.post(f"/api/capture/{capture_id}/discard") or {"ok": True}

    # --- notes ---

    @mcp.tool(annotations=READ)
    async def list_notes(limit: int = 200) -> list[dict[str, Any]]:
        """The owner's pages (ideas, plans, meeting notes), newest first;
        parent_id gives the tree. Use search_notes to find one by content."""
        rows = await api.get("/api/notes", limit=max(1, min(limit, 1000)))
        return [compact_note(n) for n in rows]

    @mcp.tool(annotations=READ)
    async def search_notes(q: str, limit: int = 10) -> list[dict[str, Any]]:
        """Full-text search over the owner's pages (quotes for a phrase,
        -word to exclude). Returns a highlighted snippet per hit."""
        return await api.get("/api/notes/search", q=q, limit=max(1, min(limit, 50)))

    @mcp.tool(annotations=READ)
    async def read_note(note_id: str) -> dict[str, Any]:
        """A page's full text as markdown."""
        note_id = require_id("note_id", note_id)
        n = await api.get(f"/api/notes/{note_id}")
        return {**compact_note(n), "markdown": n.get("body_md") or ""}

    @mcp.tool(annotations=WRITE)
    async def write_note(title: str, markdown: str, parent_id: str | None = None,
                         person_id: str | None = None,
                         opportunity_id: str | None = None) -> dict[str, Any]:
        """Create a page from markdown (headings, lists, - [ ] tasks, links).
        parent_id nests it under another page; person_id / opportunity_id
        attach it to a contact or a deal."""
        body = _clean({"title": title.strip(), "body_md": markdown,
                       "parent_id": require_id("parent_id", parent_id) if parent_id else None,
                       "person_id": require_id("person_id", person_id) if person_id else None,
                       "opportunity_id": require_id("opportunity_id", opportunity_id) if opportunity_id else None})
        return compact_note(await api.post("/api/notes", json=body))

    @mcp.tool(annotations=WRITE)
    async def append_to_note(note_id: str, markdown: str) -> dict[str, Any]:
        """Add markdown at the end of an existing page — a decision, a
        follow-up thought, today's entry in a running log."""
        note_id = require_id("note_id", note_id)
        return compact_note(await api.post(f"/api/notes/{note_id}/append", json={"markdown": markdown}))

    # --- the owner's phone ---

    @mcp.tool(annotations=SEND)
    async def notify_me(text: str) -> dict[str, Any]:
        """Send the owner a short plain-text Telegram message on their own
        phone (the plan for the day, a reminder, a question). Plain text,
        no markdown; keep it under a screen."""
        if not (cfg.tg_bot_token and cfg.tg_chat_id):
            raise ToolError("Telegram is not configured on this server (ALERT_TG_BOT_TOKEN / ALERT_TG_CHAT_ID)")
        text = text.strip()
        if not text:
            raise ToolError("empty message")
        if len(text) > MAX_NOTIFY_CHARS:
            text = text[:MAX_NOTIFY_CHARS - 1] + "…"
        async with httpx.AsyncClient(timeout=20, transport=transport) as hc:
            r = await hc.post(f"https://api.telegram.org/bot{cfg.tg_bot_token}/sendMessage",
                              json={"chat_id": cfg.tg_chat_id, "text": text,
                                    "disable_web_page_preview": True})
        if r.status_code >= 400:
            raise ToolError(f"telegram refused the message ({r.status_code}): {r.text[:200]}")
        return {"sent": True, "chars": len(text)}
