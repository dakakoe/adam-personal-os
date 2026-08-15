"""FastAPI app for the merge UI.

Endpoints:
  POST /api/login              - exchange bearer token for httpOnly cookie
  POST /api/logout             - clear cookie
  GET  /api/health             - public, no auth

  GET  /api/persons?q&limit&offset
  GET  /api/persons/{id}
  POST /api/persons/{id}/identities       body: IdentityCreate
  DELETE /api/persons/{id}/identities/{identity_id}

  GET  /api/capture/linkedin/{vanity}     - already known?
  POST /api/capture/linkedin              body: LinkedInCapture (browser extension)

  GET  /api/merge/candidates?limit&offset
  GET  /api/merge/candidates/{id}
  POST /api/merge/candidates/{id}/decision  body: MergeDecisionIn
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

import asyncio
import hashlib
import os.path
import re
import secrets

import httpx
from datetime import date, datetime, time, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from . import config as cfg_mod
from . import auth, db, queries, extraction, gcal_write, gmail_send, gmail_fetch, oauth_reconnect, rspamd, setup_flow, finance_categorize
from .models import (
    CompanyCreate, CompanyDetail, CompanyMergeIn, CompanyPatch, CompanyPersonIn,
    CompanyRow, DailyPlanRow, DraftGenerateIn, DraftPatch, DraftRow, GranolaIngest,
    CaptureIn, CaptureConfirmIn, CaptureReclassifyIn, CaptureInviteIn, CapturePatchIn,
    IdentityCreate, IdentityRow, LinkSuggestionDismissIn, LoginIn,
    MergeCandidateRow, MergeDecisionIn, NameSuggestion, OpportunityCreate,
    OpportunityDetail, OpportunityEventIn, OpportunityPatch, OpportunityRow,
    IdentityRolePatch, ContactCircle, ContactCircleIn, ContactCirclePatch,
    PersonCirclesIn, CircleDueRow,
    FollowupRow, FollowupCreate, FollowupPatch, FollowupNoteIn,
    FollowupBulkIds, FollowupBulkPatch, FollowupBulkResult,
    LinkedInCapture, LinkedInCaptureLookup, LinkedInCaptureResult,
    OpportunityStageIn, PersonDetail, PersonRow, PersonSensitivity, ContactSharing, ProjectCreate,
    ProspectRow,
    ProjectDetail, ProjectMemberIn, ProjectPatch, ProjectRow, RenameIn, BirthdayIn,
    SignalRow, SourceSecretIn, SourceStatusIn, SubtasksCreateIn, SuggestionReassignIn, SuggestionRow,
    TelegramSignInIn,
    TaskCreate, TaskDetail, TaskPatch, TaskPersonIn, TaskRow,
    RecurringTaskRow, RecurringTaskIn, RecurringTaskPatch, CalendarTargetIn,
    WeeklyGoalIn, StageConfigRow, StageCreateIn, StagePatchIn, StageReorderIn,
    FinAssetRow, FinAssetCreate, FinAssetPatch,
    FinAccountRow, FinAccountCreate, FinAccountPatch,
    FinMemberRow, FinMemberCreate, FinMemberPatch,
    FinApprovalRow, FinApprovalDecision,
    MailAccount, MailThreadRow, MailMessage, MailSendIn, MailStateIn, MailClassIn,
    MailSenderRow, MailSenderActIn, MailCleanupSenderRow, MailSenderUnsubIn, MailBulkActIn,
    MailSenderKeepIn, MailSenderClearIn,
    FinCategoryRow, FinCategoryCreate, FinCategoryPatch,
    FinTransactionRow, FinTransactionCreate, FinTransactionPatch,
    NetWorthRow, FinImportBatchRow, ImportConfirmIn,
    FinPayeeRow, FinPayeeCreate, FinPayeePatch, FinBudgetRow, FinBudgetUpsert,
    FinPlannedRow, FinPlannedCreate, FinPlannedPatch,
    FinHoldingRow, FinHoldingUpsert,
    FinLotRow, FinLotCreate, FinSaleRow, FinSaleCreate, FinPositionRow,
)

log = logging.getLogger(__name__)


def _is_substantive_reason(reason: str) -> bool:
    """Gate for person_mention follow-ups (suppress-when-no-reason policy).
    Rejects empty, too-short, or generic 'was mentioned' filler so only a
    concrete reason-to-reach-out survives."""
    r = (reason or "").strip().lower()
    if len(r) < 8:
        return False
    # Generic non-reasons the model sometimes emits despite instructions.
    filler = ("mentioned", "was discussed", "came up", "name came up", "no specific", "n/a")
    return not any(r.startswith(f) or r == f for f in filler)


def _parse_due_date(v) -> date | None:
    """A capture's task due_date is an LLM-emitted 'YYYY-MM-DD' string; the
    task table column is DATE so asyncpg needs a date object."""
    if not v or not isinstance(v, str):
        return None
    try:
        return date.fromisoformat(v.strip()[:10])
    except ValueError:
        return None


_BIRTHDAY_MMDD_RE = re.compile(r"^(\d{1,2})[-/](\d{1,2})$")


def _parse_birthday(v) -> date | None:
    """'YYYY-MM-DD' or 'MM-DD' (year → 1900 sentinel) → date; None/empty clears.
    Raises 400 when a non-empty value can't be parsed."""
    if not v or not isinstance(v, str) or not v.strip():
        return None
    s = v.strip()
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        pass
    m = _BIRTHDAY_MMDD_RE.match(s)
    if m:
        try:
            return date(1900, int(m.group(1)), int(m.group(2)))
        except ValueError:
            pass
    raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="birthday must be YYYY-MM-DD or MM-DD")


_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def _norm_time(v: str) -> str | None:
    """Normalize a 'HH:MM' clock string (zero-padded), or None if unparseable.
    No tz math — this is a wall-clock reading the server later localizes."""
    if not isinstance(v, str):
        return None
    m = _TIME_RE.match(v.strip())
    if not m:
        return None
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def _assemble_event_times(parsed: dict) -> None:
    """Build start/end ISO strings from the atomic event_date/event_time/
    event_end_time fields (the model never builds a datetime — the tz gotcha;
    the server owns assembly + localization). Mutates parsed; no-op off-event."""
    if parsed.get("type") != "event":
        return
    d = (parsed.get("event_date") or "").strip()
    tm = (parsed.get("event_time") or "").strip()
    et = (parsed.get("event_end_time") or "").strip()
    if parsed.get("all_day") and d:
        parsed["start"], parsed["end"] = d, ""
    elif d and tm:
        parsed["start"] = f"{d}T{tm}:00"
        parsed["end"] = f"{d}T{et}:00" if et else ""
    else:
        parsed["start"] = parsed.get("start") or ""
        parsed["end"] = parsed.get("end") or ""
    parsed["tz"] = parsed.get("tz") or "Asia/Bangkok"


def _is_finance_scope(owner: str | None) -> bool:
    """Household members other than the primary owner ('me') are scoped to
    spending only — they can log expense/income but never touch the CRM,
    contacts, projects, or calendar. Single source of truth for that policy."""
    return owner in ("wife", "son")


def _fmt_capture_dt(iso) -> str:
    if not iso or not isinstance(iso, str):
        return ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso
    return dt.strftime("%b %-d %H:%M")


_DOW_NAME = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_DOW3 = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _parse_clock(v) -> time | None:
    """'HH:MM' → datetime.time, or None."""
    if not v or not isinstance(v, str):
        return None
    m = _TIME_RE.match(v.strip())
    if not m:
        return None
    return time(int(m.group(1)), int(m.group(2)))


def _humanize_repeat(repeat: str, weekdays, at_time) -> str:
    """Plain-text recurrence for the bot card, e.g. 'Every weekday at 12:00'."""
    at = f" at {at_time}" if at_time else ""
    if repeat == "daily":
        return f"Every day{at}"
    if repeat == "weekdays":
        return f"Every weekday{at}"
    if repeat == "weekly":
        days = ", ".join(_DOW3[d] for d in (weekdays or []) if 0 <= d <= 6)
        return f"Every {days}{at}" if days else f"Weekly{at}"
    if repeat == "monthly":
        return f"Monthly{at}"
    if repeat == "yearly":
        return f"Yearly{at}"
    return ""


def _render_capture_summary(p: dict) -> str:
    """Plain-text card the bot echoes verbatim (no parse_mode — matches the
    alerter's deliberate plain-text choice)."""
    t = p.get("type")
    bits: list[str] = []
    if t == "tasks":
        items = p.get("items") or []
        proj = p.get("project_name") or "Shopping List"
        lines = [f"📋 {len(items)} task{'s' if len(items) != 1 else ''} → {proj}"]
        lines += [f"• {it}" for it in items[:20]]
        if len(items) > 20:
            lines.append(f"…and {len(items) - 20} more")
        return "\n".join(lines)
    if t == "task":
        head = "📋 Task"
        rep = p.get("repeat") or "none"
        tm = (p.get("task_time") or "").strip()
        if rep != "none":
            bits.append("🔁 " + _humanize_repeat(rep, p.get("repeat_weekdays"), tm))
        else:
            when = ""
            if p.get("due_date"):
                when = f"Due {p['due_date']}"
            if tm:
                when = f"{when} {tm}".strip() if when else tm
            if when:
                bits.append(when)
        if p.get("duration_min"):
            bits.append(f"{p['duration_min']}m")
        if p.get("calendar_account"):
            bits.append(f"📅 {p['calendar_account']}")
    elif t == "opportunity":
        head = "💼 Opportunity"
        if p.get("stage"):
            bits.append(f"Stage: {p['stage']}")
        if p.get("value"):
            bits.append(str(p["value"]))
    elif t == "event":
        head = "📅 Event"
        if p.get("all_day"):
            bits.append(f"{(p.get('start') or '')[:10]} (all day)")
        else:
            s, e = _fmt_capture_dt(p.get("start")), _fmt_capture_dt(p.get("end"))
            when = s + (f"–{e.split(' ')[-1] if e else ''}" if e else "")
            tz = p.get("tz")
            if tz and tz != "Asia/Bangkok":
                when += f" ({tz.split('/')[-1].replace('_', ' ')})"
            if when.strip():
                bits.append(when)
        if p.get("conference"):
            bits.append("🎥 Google Meet")
        elif p.get("location"):
            bits.append(str(p["location"]))
    elif t in ("expense", "income"):
        head = "💸 Expense" if t == "expense" else "💰 Income"
        amt = p.get("amount") or 0
        bits.append(f"{amt:g} {p.get('currency') or 'THB'}")
        if p.get("category_label"):
            bits.append(f"🏷 {p['category_label']}")
        if p.get("payee"):
            bits.append(f"@ {p['payee']}")
        if p.get("account_name"):
            bits.append((f"from {p['account_name']}" if t == "expense" else f"to {p['account_name']}"))
        else:
            bits.append("⚠️ pick an account")
        if p.get("txn_date"):
            bits.append(p["txn_date"])
    elif t == "transfer":
        head = "🔀 Transfer"
        amt = p.get("amount") or 0
        bits.append(f"{amt:g} {p.get('currency') or 'THB'}")
        src = p.get("from_account_name") or "⚠️ set source"
        dst = p.get("to_account_name") or "⚠️ set destination"
        bits.append(f"{src} → {dst}")
        if p.get("txn_date"):
            bits.append(p["txn_date"])
    else:
        head = "📝 Note"
    if p.get("project_name"):
        bits.append(f"Project: {p['project_name']}")
    # Events carry the person in the "the user <> Name" title already; for
    # task/opp show the matched CRM contact (raw 'Brian Kong' → 'Brian Kang'),
    # flagging an unmatched name so it's clear nothing was linked.
    if t != "event":
        if p.get("person_matched") and p.get("person_display"):
            bits.append(f"With: {p['person_display']}")
        elif p.get("person_name"):
            bits.append(f"With: {p['person_name']} (new contact)")
    elif p.get("person_name") and not p.get("person_matched"):
        bits.append("(new contact)")
    lines = [head, p.get("title") or "(untitled)"]
    if bits:
        lines.append(" · ".join(bits))
    return "\n".join(lines)


def _retitle_event_for_person(parsed: dict, who: str | None) -> None:
    """Events carry a 'the user <> Name' title derived from the person; keep it in
    sync when the person is edited. No-op for non-events."""
    if parsed.get("type") == "event" and who:
        parsed["title"] = f"the user <> {who}"


# --- task → calendar sync helpers -----------------------------------------

TASK_EVENT_TZ = "Asia/Bangkok"


def _task_event_params(task: dict):
    """(summary, description, start, end, all_day) from a task's due date/time,
    or None when the task has no date (can't place it on a calendar)."""
    due = task.get("due_date")
    if not due:
        return None
    ds = due.isoformat() if hasattr(due, "isoformat") else str(due)[:10]
    tm = task.get("due_time")
    if tm is None:
        return (task.get("title"), task.get("description"), ds, "", True)
    ts = tm.strftime("%H:%M:%S") if hasattr(tm, "strftime") else str(tm)[:8]
    return (task.get("title"), task.get("description"), f"{ds}T{ts}", "", False)


async def _sync_task_calendar(pool, cfg, task: dict, account: str,
                              calendar_id: str | None = None) -> dict:
    """Create/update the task's mirror event on `account`'s chosen calendar
    (None = primary). Moving accounts OR calendars deletes the old event first.
    Persists the linkage."""
    params = _task_event_params(task)
    if params is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="give the task a due date before adding it to a calendar")
    summary, desc, start, end, all_day = params
    cal = calendar_id or "primary"
    old_acct, old_eid = task.get("gcal_account"), task.get("gcal_event_id")
    old_cal = task.get("gcal_calendar_id") or "primary"
    moved = old_acct != account or old_cal != cal
    if old_eid and old_acct and moved:
        try:
            await gcal_write.delete_task_event(
                pool, client_secrets_path=cfg.gcal_client_secrets,
                account_email=old_acct, event_id=old_eid, calendar_id=old_cal)
        except Exception:
            log.warning("could not delete old calendar event when moving task %s", task.get("id"))
    reuse_eid = old_eid if not moved else None
    res = await gcal_write.upsert_task_event(
        pool, client_secrets_path=cfg.gcal_client_secrets, account_email=account,
        event_id=reuse_eid, summary=summary, description=desc,
        start=start, end=end, all_day=all_day, tz=TASK_EVENT_TZ,
        duration_min=task.get("duration_min"), calendar_id=cal)
    await queries.set_task_calendar(pool, task["id"], account, res["event_id"],
                                    res.get("html_link"), calendar_id)
    return res


async def _unsync_task_calendar(pool, cfg, task: dict) -> None:
    """Remove the task's calendar event (if any) and clear the linkage."""
    if task.get("gcal_event_id") and task.get("gcal_account"):
        try:
            await gcal_write.delete_task_event(
                pool, client_secrets_path=cfg.gcal_client_secrets,
                account_email=task["gcal_account"], event_id=task["gcal_event_id"],
                calendar_id=task.get("gcal_calendar_id") or "primary")
        except Exception:
            log.warning("could not delete calendar event for task %s", task.get("id"))
    await queries.clear_task_calendar(pool, task["id"])


# --- routine → recurring calendar event (RRULE) ---------------------------

_RRULE_DAY = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]   # 0=Mon … 6=Sun


def _routine_event_params(routine: dict):
    """(summary, description, start, end, all_day, rrule) for a routine's single
    recurring event. start = first occurrence on/after anchor so DTSTART is valid
    for weekly BYDAY rules."""
    anchor = routine["anchor_date"]
    freq = routine["freq"]
    if freq == "daily":
        rrule = "RRULE:FREQ=DAILY"
        start_date = anchor
    elif freq == "weekly":
        wd = sorted({d for d in (routine.get("byweekday") or []) if 0 <= d <= 6})
        if not wd:
            wd = [0, 1, 2, 3, 4]
        rrule = "RRULE:FREQ=WEEKLY;BYDAY=" + ",".join(_RRULE_DAY[d] for d in wd)
        start_date = anchor
        for i in range(7):
            if (anchor + timedelta(days=i)).weekday() in wd:
                start_date = anchor + timedelta(days=i)
                break
    elif freq == "monthly":
        rrule = f"RRULE:FREQ=MONTHLY;BYMONTHDAY={anchor.day}"
        start_date = anchor
    else:  # yearly
        rrule = "RRULE:FREQ=YEARLY"
        start_date = anchor
    ds = start_date.isoformat()
    at = routine.get("at_time")
    summary, desc = routine.get("title"), routine.get("description")
    if at is None:
        return (summary, desc, ds, "", True, rrule)
    ts = at.strftime("%H:%M:%S") if hasattr(at, "strftime") else str(at)[:8]
    return (summary, desc, f"{ds}T{ts}", "", False, rrule)


async def _sync_routine_calendar(pool, cfg, routine: dict, account: str,
                                 calendar_id: str | None = None) -> dict:
    summary, desc, start, end, all_day, rrule = _routine_event_params(routine)
    cal = calendar_id or "primary"
    # Participants ride the single RRULE series → one invite for the whole
    # routine. Sensitive contacts + those without an email are filtered out here.
    emails = gcal_write.eligible_invite_emails(routine.get("participants") or [])
    old_acct, old_eid = routine.get("gcal_account"), routine.get("gcal_event_id")
    old_cal = routine.get("gcal_calendar_id") or "primary"
    moved = old_acct != account or old_cal != cal
    if old_eid and old_acct and moved:
        try:
            await gcal_write.delete_task_event(
                pool, client_secrets_path=cfg.gcal_client_secrets,
                account_email=old_acct, event_id=old_eid, calendar_id=old_cal,
                notify=bool(emails))   # cancel the old series for its invitees
        except Exception:
            log.warning("could not delete old recurring event when moving routine %s", routine.get("id"))
    reuse = old_eid if not moved else None
    res = await gcal_write.upsert_recurring_event(
        pool, client_secrets_path=cfg.gcal_client_secrets, account_email=account,
        event_id=reuse, summary=summary, description=desc, start=start, end=end,
        all_day=all_day, rrule=rrule, tz=TASK_EVENT_TZ,
        duration_min=routine.get("duration_min"), calendar_id=cal,
        attendee_emails=emails)
    await queries.set_routine_calendar(pool, routine["id"], account, res["event_id"],
                                       res.get("html_link"), calendar_id)
    return res


async def _unsync_routine_calendar(pool, cfg, routine: dict) -> None:
    if routine.get("gcal_event_id") and routine.get("gcal_account"):
        notify = bool(gcal_write.eligible_invite_emails(routine.get("participants") or []))
        try:
            await gcal_write.delete_task_event(
                pool, client_secrets_path=cfg.gcal_client_secrets,
                account_email=routine["gcal_account"], event_id=routine["gcal_event_id"],
                calendar_id=routine.get("gcal_calendar_id") or "primary", notify=notify)
        except Exception:
            log.warning("could not delete recurring event for routine %s", routine.get("id"))
    await queries.clear_routine_calendar(pool, routine["id"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = cfg_mod.load()
    app.state.cfg = cfg
    app.state.pool = await db.connect(cfg.db_url)
    # Lazy Anthropic client for Granola extraction (Phase 2). None when no
    # key is configured — the ingest endpoint 503s rather than crashing.
    app.state.anthropic = None
    if cfg.anthropic_api_key:
        try:
            import anthropic
            app.state.anthropic = anthropic.AsyncAnthropic(api_key=cfg.anthropic_api_key)
        except Exception:
            log.exception("anthropic client init failed (Granola ingest disabled)")
    log.info("merge_api up: db=%s host=%s:%d anthropic=%s",
             cfg.db_url.split("@")[-1], cfg.host, cfg.port,
             "on" if app.state.anthropic else "off")
    # Sweep stale pending candidates before serving the first request.
    # Cheap, idempotent. Three filters:
    #   - zombie:       one side has been merged away (no msgs/ids left)
    #   - incompatible: both sides own different telegram/linkedin
    #                   accounts → can't be the same person
    #   - weak_fuzzy:   fuzzy_name pairs that don't satisfy the 2-token
    #                   shape rule (one-token side or surname mismatch)
    try:
        nz = await queries.auto_reject_zombie_candidates(app.state.pool)
        ni = await queries.auto_reject_incompatible_candidates(app.state.pool)
        nw = await queries.auto_reject_weak_fuzzy_name_candidates(app.state.pool)
        if nz or ni or nw:
            log.info(
                "auto-rejected %d zombie + %d incompatible + %d weak fuzzy_name candidates",
                nz, ni, nw,
            )
    except Exception:
        log.exception("merge candidate sweep failed (non-fatal)")
    try:
        yield
    finally:
        await app.state.pool.close()


# Exact project/task endpoints a budget caller may reach. Deliberately precise
# (method + single-segment paths) so task SUB-routes (/decompose, /subtasks,
# /people, /calendar) and project mutations (/members, /recaps, PATCH/DELETE)
# stay 403. Data-scoping to her memberships happens again in the handlers.
_BUDGET_PROJECT_RE = re.compile(r"^/api/projects/[^/]+$")
_BUDGET_TASK_RE = re.compile(r"^/api/tasks/[^/]+$")
# single-segment only: matches /api/persons/{id} and /api/persons/count, but NOT
# the /api/persons/{id}/<subroute> endpoints (identities, drafts, merge, photo,
# …) which stay admin-only. Contact reads are scoped by the visibility predicate.
_BUDGET_PERSON_RE = re.compile(r"^/api/persons/[^/]+$")

# Sender bulk-act mapping: local mail_state overlay + Gmail batchModify labels
# per action. trash sets the trashed flag (NOT archived — it feeds the Trash
# view) and moves the mail to Gmail Trash, where Google purges it after ~30d.
_SENDER_ACT_OVERLAY = {"read": {"read": True},
                       "archive": {"archived": True},
                       "trash": {"trashed": True}}
_SENDER_ACT_LABELS = {"read": ([], ["UNREAD"]),
                      "archive": ([], ["INBOX"]),
                      "trash": (["TRASH"], ["INBOX"])}


def _budget_api_allowed(method: str, path: str) -> bool:
    """API method+path a budget-role (member) caller may reach — everything else
    under /api is admin-only."""
    if path.startswith("/api/finance/") or path in ("/api/login", "/api/logout", "/api/me"):
        return True
    if method == "GET" and (path == "/api/projects" or _BUDGET_PROJECT_RE.match(path)):
        return True
    if path == "/api/tasks" and method in ("GET", "POST"):
        return True
    if _BUDGET_TASK_RE.match(path) and method in ("GET", "PATCH", "DELETE"):
        return True
    # contacts: read-only, scoped to shared/own (unified-sharing — contacts)
    if method == "GET" and (path == "/api/persons" or _BUDGET_PERSON_RE.match(path)):
        return True
    # companies: ONLY the reduced/scoped list — NOT /{id} (detail carries
    # opportunity/pipeline data that stays owner-only) nor link-suggestions.
    if method == "GET" and path == "/api/companies":
        return True
    return False


async def _budget_member_or_403(pool, cfg, project_id: str | None) -> None:
    """For a budget caller, require membership of `project_id` (slug or id); else
    403. The data gate behind the middleware's endpoint allow-list."""
    if not await queries.is_project_member(pool, cfg.budget_person_id, project_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="not one of your projects")


async def _txn_legs_visible(pool, viewer, txn: dict) -> bool:
    """A scoped member may touch a transaction iff at least one of its legs is on
    an account they can see (shared or owned). viewer=None → app-owner, always OK."""
    if not viewer:
        return True
    for acc in (txn.get("outflow_account_id"), txn.get("inflow_account_id")):
        if acc and await queries.member_can_see_account(pool, viewer, acc):
            return True
    return False


async def _acting_member_id(request, pool) -> str | None:
    """The fin_member id of the current caller, for attribution (works for owners
    too). Authelia email when present, else the seeded token member (admin→owner,
    budget→member). Used to stamp who requested/decided an approval."""
    email = auth._proxy_email(request.app.state.cfg, request)
    m = await queries.get_member_by_email(pool, email) if email else None
    if not m:
        actor = "owner" if auth.role_for_request(request) == "admin" else "member"
        m = await queries.get_member_by_actor(pool, actor)
    return m["id"] if m else None


class RoleActorMiddleware:
    """Raw ASGI middleware (NOT BaseHTTPMiddleware — that can drop contextvars
    across tasks). Two jobs, both needing to run in the request's own task:
      1. stamp db.current_actor ('owner'/'member'/'system') so the fin_audit
         triggers attribute the change to the right person;
      2. enforce the budget role — a budget token may only reach /api/finance/*
         (+ login/logout); anything else under /api → 403."""

    _ACTOR = {"admin": "owner", "budget": "member"}

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive=receive)
        role = auth.role_for_request(request)
        token = db.current_actor.set(self._ACTOR.get(role, "system"))
        try:
            path = scope.get("path", "")
            if role == "budget" and path.startswith("/api/") and not _budget_api_allowed(scope.get("method", ""), path):
                await JSONResponse({"detail": "forbidden for this account"},
                                   status_code=403)(scope, receive, send)
                return
            await self.app(scope, receive, send)
        finally:
            db.current_actor.reset(token)


def create_app() -> FastAPI:
    app = FastAPI(title="memory merge API", lifespan=lifespan)
    app.add_middleware(RoleActorMiddleware)

    # CORS only enabled when MERGE_API_CORS_ORIGIN is set (local dev).
    # Production serves UI from same domain so cross-origin is unnecessary.
    @app.on_event("startup")
    async def _wire_cors():
        cors_origin = app.state.cfg.cors_origin
        if cors_origin:
            app.add_middleware(
                CORSMiddleware,
                allow_origins=[cors_origin],
                allow_credentials=True,
                allow_methods=["*"],
                allow_headers=["*"],
            )

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    # --- version / update check ------------------------------------------
    # The public mirror is force-pushed as ONE commit, so an install can't use
    # git history to tell whether it's current. publish-public.sh bakes a
    # VERSION file into the published tree and cuts a GitHub Release; we compare
    # the two. A private/dev checkout has no VERSION file → update_available is
    # always False, so this stays invisible on the source instance.
    _VERSION_CACHE: dict = {"checked_at": 0.0, "latest": None, "notes": None, "url": None}
    _VERSION_TTL = 6 * 3600     # GitHub allows 60 unauthenticated req/h; 4/day is plenty

    def _installed_version() -> str | None:
        for p in ("/srv/memory/VERSION",
                  os.path.join(os.path.dirname(__file__), "..", "..", "VERSION")):
            try:
                v = open(p, encoding="utf-8").read().strip()
                if v:
                    return v
            except OSError:
                continue
        return None

    @app.get("/api/version")
    async def version_route(_: None = Depends(auth.verify)):
        """{current, latest, update_available, notes, url}. Fail-soft: if GitHub
        is unreachable we report the installed version and no update, never an
        error — an update check must not be able to break the dashboard."""
        current = _installed_version()
        repo = os.environ.get("ADAM_UPDATE_REPO", "dakakoe/adam-personal-os")
        now = asyncio.get_event_loop().time()
        if current and repo and now - _VERSION_CACHE["checked_at"] > _VERSION_TTL:
            try:
                async with httpx.AsyncClient(timeout=8) as hc:
                    r = await hc.get(f"https://api.github.com/repos/{repo}/releases/latest",
                                     headers={"Accept": "application/vnd.github+json"})
                    if r.status_code == 200:
                        d = r.json()
                        _VERSION_CACHE.update(
                            latest=(d.get("tag_name") or "").lstrip("v") or None,
                            notes=(d.get("body") or "").strip()[:2000] or None,
                            url=d.get("html_url"))
                    _VERSION_CACHE["checked_at"] = now
            except Exception:  # noqa: BLE001 — offline/rate-limited is not an error
                _VERSION_CACHE["checked_at"] = now
        latest = _VERSION_CACHE["latest"]
        # CalVer strings compare correctly as tuples of ints (2026.8.2 < 2026.8.10).
        def _key(v: str):
            return tuple(int(x) for x in re.findall(r"\d+", v or ""))
        update = bool(current and latest and _key(latest) > _key(current))
        return {"current": current, "latest": latest, "update_available": update,
                "notes": _VERSION_CACHE["notes"] if update else None,
                "url": _VERSION_CACHE["url"] if update else None}

    # Units to watch. always_on → healthy when ActiveState=active; oneshot
    # (timer-driven) → healthy when the last run exited 0 (ExecMainStatus).
    HEALTH_UNITS = [
        ("memory-merge-api.service", "Merge API", "always_on"),
        ("memory-merge-ui.service", "Merge UI", "always_on"),
        ("memory-telethon.service", "Telegram ingest", "always_on"),
        ("memory-mcp.service", "MCP server", "always_on"),
        ("memory-normalizer.service", "Normalizer", "oneshot"),
        ("memory-gmail.service", "Gmail sync", "oneshot"),
        ("memory-gcal.service", "Calendar sync", "oneshot"),
        ("memory-granola.service", "Granola poll", "oneshot"),
        ("memory-interaction-scan.service", "Interaction scanner", "oneshot"),
        ("memory-daily-plan.service", "Daily planner", "oneshot"),
        ("memory-digest.service", "Daily digest", "oneshot"),
        ("memory-profile-refresh.service", "Profile refresh", "oneshot"),
        ("memory-routines.service", "Routine generator", "oneshot"),
        ("memory-enrich-companies.service", "Company backfill", "oneshot"),
        ("memory-fin-fx.service", "Budget FX rates", "oneshot"),
        ("memory-fin-planned.service", "Planned transactions", "oneshot"),
        ("memory-backup.service", "Backup (off-site)", "oneshot"),
        ("memory-alert.service", "Push alerts", "oneshot"),
        ("memory-telegram-bot.service", "Capture bot", "always_on"),
    ]

    async def _show(unit: str, props: str) -> str:
        """Raw `systemctl show` output (newline KEY=VALUE pairs). Returned as
        text — not a dict — because some properties (e.g. TimersMonotonic)
        legitimately repeat and a dict would clobber all but the last."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "show", unit, f"--property={props}",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            return out.decode("utf-8", "replace")
        except Exception:
            return ""

    def _prop(text: str, key: str) -> str:
        m = re.search(rf"^{re.escape(key)}=(.*)$", text, re.MULTILINE)
        return m.group(1).strip() if m else ""

    _DUR_UNITS = {  # systemd duration tokens → seconds
        "us": 1e-6, "ms": 1e-3, "s": 1, "sec": 1, "m": 60, "min": 60,
        "h": 3600, "hr": 3600, "d": 86400, "w": 604800,
    }

    def _parse_duration(s: str):
        """'3h' / '15min' / '1h 30min' → timedelta. None if unparseable."""
        from datetime import timedelta
        total = 0.0
        found = False
        for num, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(us|ms|min|sec|hr|[smhdw])", s):
            mult = _DUR_UNITS.get(unit)
            if mult is None:
                continue
            total += float(num) * mult
            found = True
        return timedelta(seconds=total) if found else None

    def _parse_ts(s: str):
        """systemd timestamp 'Mon 2026-06-01 02:14:52 UTC' → aware UTC datetime.
        The droplet runs UTC, so the tz token is informational. None if empty."""
        from datetime import datetime, timezone
        parts = (s or "").split()
        if len(parts) < 3:
            return None
        try:
            dt = datetime.strptime(f"{parts[1]} {parts[2]}", "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
        return dt.replace(tzinfo=timezone.utc)

    # How many missed firings before a still-"green" oneshot is called overdue.
    OVERDUE_FACTOR = float(os.environ.get("HEALTH_OVERDUE_FACTOR", "3"))

    async def _timer_health(timer_unit: str, now):
        """Resolve a oneshot's .timer: is its schedule still alive? Returns
        (state, last_fired, overdue_bool). A unit can exit 0 forever yet never
        run again if its timer was disabled/masked/failed — last-exit-0 alone
        misses that, so we check the timer too."""
        from datetime import timedelta
        text = await _show(
            timer_unit,
            "LoadState,ActiveState,LastTriggerUSec,NextElapseUSecRealtime,TimersMonotonic",
        )
        if _prop(text, "LoadState") != "loaded":
            return (None, None, False)  # no timer for this unit
        state = _prop(text, "ActiveState") or None
        last_fired = _parse_ts(_prop(text, "LastTriggerUSec"))
        # Recurring period: monotonic timers expose OnUnitActiveUSec; calendar
        # timers we infer from (next fire − last fire).
        interval = None
        mono = re.search(r"OnUnitActiveUSec=(\S+)", text)
        if mono:
            interval = _parse_duration(mono.group(1))
        if interval is None:
            nxt = _parse_ts(_prop(text, "NextElapseUSecRealtime"))
            if nxt and last_fired and nxt > last_fired:
                interval = nxt - last_fired
        overdue = False
        if state == "active" and interval and last_fired:
            # Floor at 30 min so a 5-min timer doesn't flap on scheduling jitter.
            threshold = max(interval * OVERDUE_FACTOR, timedelta(minutes=30))
            overdue = (now - last_fired) > threshold
        return (state, last_fired, overdue)

    @app.get("/api/health/system")
    async def system_health(request: Request, _: None = Depends(auth.verify)):
        """Per-worker health from systemd (last run + exit status + timer
        liveness) so silent failures — a stalled scanner, a failed backup, an
        out-of-credits LLM run, or a dead timer that stops a unit running at
        all — surface instead of going unnoticed."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)

        async def _gather(unit: str, kind: str):
            text = await _show(
                unit,
                "ActiveState,SubState,Result,ExecMainStatus,ExecMainExitTimestamp,ActiveEnterTimestamp",
            )
            timer = (
                await _timer_health(unit.replace(".service", ".timer"), now)
                if kind == "oneshot"
                else (None, None, False)
            )
            return text, timer

        gathered = await asyncio.gather(
            *[_gather(u, kind) for u, _, kind in HEALTH_UNITS]
        )
        units = []
        for (unit, label, kind), (text, (timer_state, last_fired, overdue)) in zip(
            HEALTH_UNITS, gathered
        ):
            active = _prop(text, "ActiveState")
            exit_status = _prop(text, "ExecMainStatus")
            result = _prop(text, "Result")
            reason = None
            if kind == "always_on":
                ok = active == "active"
                if not ok:
                    reason = active or "inactive"
                last_run = _prop(text, "ActiveEnterTimestamp") or None
            else:
                last_run = _prop(text, "ExecMainExitTimestamp") or None
                # Failed last run is the most actionable; then a dead/overdue
                # timer; then the normal exited-0 / never-run states.
                if exit_status not in ("", "0"):
                    ok, reason = False, f"exit {exit_status}"
                elif timer_state is not None and timer_state != "active":
                    ok, reason = False, f"schedule stopped (timer {timer_state})"
                elif overdue:
                    ok = False
                    when = last_fired.strftime("%b %d %H:%M") + "Z" if last_fired else "?"
                    reason = f"overdue — timer last fired {when}"
                elif exit_status == "":
                    ok = None  # never run
                else:
                    ok = True
            units.append({
                "unit": unit, "label": label, "kind": kind,
                "ok": ok, "active_state": active or None,
                "result": result or None, "exit_status": exit_status or None,
                "last_run": (last_run or None),
                "reason": reason,
                "timer_state": timer_state,
                "overdue": overdue,
            })

        # DB ping
        db_ok = True
        try:
            async with request.app.state.pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
        except Exception:
            db_ok = False

        degraded = (not db_ok) or any(u["ok"] is False for u in units)
        return {
            "ok": not degraded,
            "db_ok": db_ok,
            "anthropic_configured": request.app.state.anthropic is not None,
            "units": units,
        }

    @app.get("/api/sources")
    async def sources_status(request: Request, _: None = Depends(auth.verify)):
        """Per-DATA-SOURCE status for the Sources page: item count + last-sync +
        the health of its systemd worker, and whether it needs reconnecting.
        Read-only; reuses the unit-status helpers from /api/health/system.
        (key, label, unit, kind, count_sql, last_sync_sql, noun, reauth_sql).
        reauth_sql lists the email(s) the fetchers flagged needing re-consent;
        None for sources with no OAuth/login to lose."""
        _GMAIL_REAUTH = ("SELECT email FROM raw.gmail_account "
                         "WHERE status = 'reauth_needed' ORDER BY email")
        # All OAuth accounts (not just revoked ones) so the UI can offer an
        # on-demand re-consent for a healthy account — e.g. to add a new scope.
        # Retired accounts are excluded: they're a read-only archive with no
        # token, so there is nothing to re-consent.
        _GMAIL_ACCOUNTS = ("SELECT email FROM raw.gmail_account "
                           "WHERE status = 'active' ORDER BY email")
        # Retired = sunsetted mailbox kept as a read-only archive. Its synced
        # mail/events stay fully readable; nothing syncs and it never counts as
        # a health failure. Surfaced so the UI can badge it as an archive.
        _GMAIL_RETIRED = ("SELECT email FROM raw.gmail_account "
                          "WHERE status = 'retired' ORDER BY email")
        # (key, label, unit, kind, count_sql, last_sync_sql, noun, reauth_sql, accounts_sql, retired_sql)
        SOURCES = [
            ("telegram", "Telegram", "memory-telethon.service", "always_on",
             "SELECT count(*) FROM raw.telegram_message",
             "SELECT max(ingested_at) FROM raw.telegram_message", "messages", None, None, None),
            # Gmail + GCal share one Google account (raw.gmail_account), so a
            # revoked token flags both.
            ("gmail", "Gmail", "memory-gmail.service", "oneshot",
             "SELECT count(*) FROM raw.gmail_message",
             "SELECT max(last_sync_at) FROM raw.gmail_account", "emails",
             _GMAIL_REAUTH, _GMAIL_ACCOUNTS, _GMAIL_RETIRED),
            ("gcal", "Google Calendar", "memory-gcal.service", "oneshot",
             "SELECT count(*) FROM raw.gcal_event",
             "SELECT max(ingested_at) FROM raw.gcal_event", "events",
             _GMAIL_REAUTH, _GMAIL_ACCOUNTS, _GMAIL_RETIRED),
            ("granola", "Granola", "memory-granola.service", "oneshot",
             "SELECT count(*) FROM memory.meeting_recap",
             "SELECT max(ingested_at) FROM memory.meeting_recap", "recaps", None, None, None),
            ("crypto", "Crypto wallets", "memory-fin-wallets.service", "oneshot",
             "SELECT count(*) FROM memory.fin_holding WHERE source = 'chain'",
             "SELECT max(updated_at) FROM memory.fin_holding WHERE source = 'chain'", "holdings", None, None, None),
        ]

        async def _unit_ok(unit: str, kind: str):
            text = await _show(unit, "ActiveState,ExecMainStatus")
            active = _prop(text, "ActiveState")
            exit_status = _prop(text, "ExecMainStatus")
            if not active and not exit_status:
                # systemctl unreadable (no systemd / timeout / unknown unit):
                # status is undeterminable, not failed. None → UI shows neutral.
                return None, None
            if kind == "always_on":
                return (active == "active"), (active or None)
            # oneshot: failed last run is not-ok; never-run / exited-0 are fine
            if exit_status not in ("", "0"):
                return False, f"exit {exit_status}"
            return True, (active or None)

        async def _scalar(sql: str):
            try:
                async with request.app.state.pool.acquire() as conn:
                    return await conn.fetchval(sql)
            except Exception:
                return None

        async def _str_list(sql: str | None) -> list[str]:
            if not sql:
                return []
            try:
                async with request.app.state.pool.acquire() as conn:
                    return [r[0] for r in await conn.fetch(sql)]
            except Exception:
                return []

        async def _status_row(key: str):
            """Generic per-source health from memory.source_status (Telegram,
            Granola — sources with no OAuth/account status of their own).
            Returns (status, reason); (None, None) if unset/unreadable."""
            try:
                async with request.app.state.pool.acquire() as conn:
                    row = await conn.fetchrow(
                        "SELECT status, reason FROM memory.source_status "
                        "WHERE source_key = $1", key)
                return (row["status"], row["reason"]) if row else (None, None)
            except Exception:
                return (None, None)

        async def _one(key, label, unit, kind, count_sql, sync_sql, noun, reauth_sql,
                       accounts_sql, retired_sql=None):
            (unit_ok, unit_state), count, last_sync, reauth_emails, oauth_accounts, retired_accounts, (ss_status, ss_reason) = \
                await asyncio.gather(
                    _unit_ok(unit, kind), _scalar(count_sql), _scalar(sync_sql),
                    _str_list(reauth_sql), _str_list(accounts_sql), _str_list(retired_sql),
                    _status_row(key),
                )
            needs_reconnect = bool(reauth_emails) or ss_status == "needs_attention"
            return {
                "key": key, "label": label, "unit": unit, "kind": kind,
                "noun": noun, "count": count,
                "last_sync": last_sync.isoformat() if last_sync else None,
                "unit_ok": unit_ok, "unit_state": unit_state,
                "needs_reconnect": needs_reconnect,
                # Account(s) the fetchers flagged — the UI renders one reconnect
                # button per account so each consents with the right scopes.
                "reconnect_accounts": reauth_emails,
                # Browser re-consent link — only for OAuth sources (reauth_sql set)
                # and only when the flow is configured; else the UI shows the CLI.
                "reconnect_url": ("/api/sources/oauth/start"
                                  if reauth_sql and reconnect_available else None),
                # Every OAuth account (healthy included) so the UI can offer an
                # on-demand re-consent — used to add a scope without waiting for a
                # token to break. Empty for non-OAuth sources / when not configured.
                "oauth_accounts": oauth_accounts if reconnect_available else [],
                # Sunsetted mailboxes kept as a read-only archive: synced mail
                # stays searchable, nothing syncs, never a health failure.
                "retired_accounts": retired_accounts,
                # Free-text reason for non-OAuth sources (Telegram/Granola) whose
                # recovery is manual; null for OAuth sources (they have the button/CLI).
                "reconnect_reason": ss_reason if ss_status == "needs_attention" else None,
            }

        reconnect_available = oauth_reconnect.reconnect_configured(request.app.state.cfg)
        return {"sources": list(await asyncio.gather(*[_one(*s) for s in SOURCES]))}

    # One-shot source workers that a manual "Sync now" may trigger. The unit is
    # a fixed constant looked up by `key` — NEVER built from user input — so
    # there is no command injection; the only capability granted is starting
    # these specific sync units. (Telegram is always-on, so not syncable.)
    SYNCABLE_UNITS = {
        "gmail": "memory-gmail.service",
        "gcal": "memory-gcal.service",
        "granola": "memory-granola.service",
        "crypto": "memory-fin-wallets.service",
    }

    @app.post("/api/sources/accounts/{email}/retire", status_code=204)
    async def retire_account_route(email: str, request: Request, _: None = Depends(auth.verify)):
        """Retire a sunsetted mailbox: keep everything it ever synced as a
        READ-ONLY ARCHIVE, but stop syncing it and stop it failing health.

        Everything already pulled lives in raw.gmail_message / raw.gcal_event and
        is untouched — it stays searchable and readable in the UI. We only flip
        the account's status and drop the (now-useless) refresh token, so no dead
        credential lingers. Every worker/send path filters status='active', so
        this single flip is what makes them skip it. Reversible via unretire
        (which then needs a fresh OAuth consent)."""
        async with request.app.state.pool.acquire() as conn:
            row = await conn.fetchrow(
                "UPDATE raw.gmail_account SET status = 'retired', refresh_token = NULL "
                "WHERE email = $1 RETURNING email", email)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="unknown account")
        log.info("retired google account %s (archive kept, token dropped)", email)
        return None

    @app.delete("/api/sources/accounts/{email}/retire", status_code=204)
    async def unretire_account_route(email: str, request: Request, _: None = Depends(auth.verify)):
        """Un-retire: mark the account reauth_needed so the Sources page offers a
        reconnect (the token was dropped on retire, so it must re-consent)."""
        async with request.app.state.pool.acquire() as conn:
            row = await conn.fetchrow(
                "UPDATE raw.gmail_account SET status = 'reauth_needed' "
                "WHERE email = $1 AND status = 'retired' RETURNING email", email)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no retired account with that address")
        return None

    @app.post("/api/sources/{key}/sync")
    async def sync_source(key: str, request: Request, role: str = Depends(auth.verify)):
        """Trigger a one-shot source worker now. Admin only. merge_api runs as
        `ops` (passwordless sudo); `sudo -n` never blocks on a prompt."""
        if role != "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="admin only")
        unit = SYNCABLE_UNITS.get(key)
        if not unit:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="unknown or non-syncable source")
        proc = await asyncio.create_subprocess_exec(
            "sudo", "-n", "systemctl", "start", unit,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        if proc.returncode != 0:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"failed to start {unit}: {(err or b'').decode()[:200]}",
            )
        return {"ok": True, "unit": unit}

    # DB-less workers (Granola is HTTP-only) report their own health here, over
    # the same admin-bearer channel they already use for ingest. Telegram is NOT
    # listed — its worker has a DB pool and writes memory.source_status directly.
    _STATUS_REPORTABLE = {"granola"}

    @app.post("/api/sources/{key}/status")
    async def report_source_status(
        key: str, body: SourceStatusIn, request: Request,
        role: str = Depends(auth.verify),
    ):
        """A background worker reports its own health → memory.source_status,
        which /api/sources surfaces on the Sources page. Admin only."""
        if role != "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="admin only")
        if key not in _STATUS_REPORTABLE:
            raise HTTPException(status.HTTP_404_NOT_FOUND,
                                detail="unknown or non-reportable source")
        st = body.status.strip().lower()
        if st not in ("ok", "needs_attention"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                detail="status must be 'ok' or 'needs_attention'")
        async with request.app.state.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO memory.source_status (source_key, status, reason, updated_at)
                VALUES ($1, $2, $3, now())
                ON CONFLICT (source_key) DO UPDATE
                   SET status = EXCLUDED.status, reason = EXCLUDED.reason, updated_at = now()
                """,
                key, st, (body.reason or None),
            )
        return {"ok": True}

    # Browser-based Google re-consent (Gmail/GCal). Admin only; both endpoints
    # are reached as top-level browser navigations, so the existing Authelia
    # forward-auth session gates them. CSRF is a double-submit nonce: a Lax
    # httponly cookie that must echo the `state` Google returns. Inert until
    # MERGE_OAUTH_REDIRECT_URI is configured.
    _OAUTH_STATE_COOKIE = "merge_oauth_state"
    _OAUTH_COOKIE_PATH = "/api/sources/oauth"

    @app.get("/api/sources/oauth/start")
    async def oauth_start(request: Request, account: str | None = None,
                          new: bool = False, role: str = Depends(auth.verify)):
        if role != "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="admin only")
        cfg = request.app.state.cfg
        if not oauth_reconnect.reconnect_configured(cfg):
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                detail="web reconnect not configured")
        # An account hint tailors the requested scopes (work account keeps its
        # write scopes) and pre-selects the Google account. Validate it against
        # known rows so it can't be an arbitrary login_hint.
        #
        # new=1 (setup wizard): connect a BRAND-NEW Google account — no
        # existence check, base scopes, no login_hint (the user picks the
        # account on Google's screen). Safe: both endpoints are admin-gated,
        # the state cookie covers CSRF, redirect_uri is fixed server-side, and
        # the callback UPSERTing whichever account actually consented is
        # exactly the desired "connect new account" semantics.
        scope_names = login_hint = None
        if account and not new:
            account = account.strip().lower()
            async with request.app.state.pool.acquire() as conn:
                known = await conn.fetchval(
                    "SELECT 1 FROM raw.gmail_account WHERE email = $1", account)
            if not known:
                raise HTTPException(status.HTTP_404_NOT_FOUND, detail="unknown account")
            scope_names = oauth_reconnect.scopes_for_account(cfg, account)
            login_hint = account
        elif new:
            scope_names = oauth_reconnect.scopes_for_account(cfg, None)
        state = secrets.token_urlsafe(32)
        url = oauth_reconnect.build_auth_url(cfg, state, scope_names, login_hint)
        resp = RedirectResponse(url, status_code=307)
        # SameSite=Lax (not strict): the cookie must ride the top-level GET that
        # Google sends back to /callback, which is cross-site.
        resp.set_cookie(
            key=_OAUTH_STATE_COOKIE, value=state, httponly=True, secure=True,
            samesite="lax", max_age=600, path=_OAUTH_COOKIE_PATH,
        )
        return resp

    @app.get("/api/sources/oauth/callback")
    async def oauth_callback(
        request: Request,
        code: str | None = None,
        state: str | None = None,
        error: str | None = None,
        role: str = Depends(auth.verify),
    ):
        if role != "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="admin only")
        cfg = request.app.state.cfg

        def _back(**params) -> RedirectResponse:
            qs = urlencode(params)
            r = RedirectResponse(f"/sources?{qs}", status_code=303)
            r.delete_cookie(_OAUTH_STATE_COOKIE, path=_OAUTH_COOKIE_PATH)
            return r

        cookie_state = request.cookies.get(_OAUTH_STATE_COOKIE)
        if error:
            return _back(reconnect_error=error[:80])
        if not code or not state or not cookie_state \
                or not secrets.compare_digest(state, cookie_state):
            return _back(reconnect_error="bad_state")
        try:
            refresh_token, access_token, granted_scopes = await oauth_reconnect.exchange_code(cfg, code)
            email = await oauth_reconnect.fetch_account_email(access_token)
            async with request.app.state.pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO raw.gmail_account (email, refresh_token, status, scopes)
                    VALUES ($1, $2, 'active', $3)
                    ON CONFLICT (email) DO UPDATE
                       SET refresh_token = EXCLUDED.refresh_token, status = 'active',
                           scopes = EXCLUDED.scopes
                    """,
                    email, refresh_token, granted_scopes,
                )
        except Exception as e:
            logging.getLogger(__name__).warning("oauth callback failed: %r", e)
            return _back(reconnect_error="exchange_failed")
        return _back(reconnected=email)

    # --- setup wizard (first-run source connection; Phase 4 remainder) ------
    # All admin-gated. The Telegram flow subprocesses the telethon fetcher's
    # auth commands (same venv-subprocess pattern as backfill-one) and parses
    # the marker lines via setup_flow.parse_auth_output.

    _TELETHON_PY = "/srv/memory/apps/telethon/.venv/bin/python"
    _TELETHON_CWD = "/srv/memory/apps/telethon"
    _TELETHON_UNIT = "memory-telethon.service"
    _LINKEDIN_PY = "/srv/memory/apps/linkedin_import/.venv/bin/python"
    _LINKEDIN_CWD = "/srv/memory/apps/linkedin_import"
    _IMPORTS_DIR = "/srv/memory/data/imports"

    async def _systemctl(verb: str, unit: str) -> None:
        proc = await asyncio.create_subprocess_exec(
            "sudo", "-n", "systemctl", verb, unit,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _, err = await proc.communicate()
        if proc.returncode != 0:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"systemctl {verb} {unit} failed: {(err or b'').decode()[:200]}")

    async def _telethon_auth_cmd(cmd: str, env_extra: dict[str, str],
                                 timeout: float = 90) -> setup_flow.AuthResult:
        env = os.environ.copy()
        env.update(env_extra)
        proc = await asyncio.create_subprocess_exec(
            _TELETHON_PY, "-m", "fetcher", cmd,
            cwd=_TELETHON_CWD, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT,
                                detail=f"{cmd} timed out")
        result = setup_flow.parse_auth_output((stdout or b"").decode("utf-8", errors="replace"))
        if result.status == "unknown":
            tail = ((stderr or b"") or (stdout or b"")).decode("utf-8", errors="replace")[-300:]
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR,
                                detail=f"{cmd} produced no marker: {tail}")
        return result

    @app.post("/api/setup/telegram/send-code")
    async def setup_telegram_send_code(request: Request, role: str = Depends(auth.verify)):
        """Start the Telegram sign-in: stop the live worker (it crash-loops on
        an unauthorized session AND holds the SQLite session lock — the auth
        subprocess must own the real session file so the sign-in persists),
        then request a login code. phone_code_hash round-trips via the client:
        refresh-safe, no server state, useless without the code + session."""
        if role != "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="admin only")
        try:
            body = await request.json()
        except Exception:
            body = {}
        phone = (body.get("phone") or "").strip() if isinstance(body, dict) else ""

        await _systemctl("stop", _TELETHON_UNIT)
        env = {"AUTH_PHONE": phone} if phone else {}
        result = await _telethon_auth_cmd("auth-send-code", env)
        if result.status == "already_authorized":
            await _systemctl("start", _TELETHON_UNIT)
            return {"ok": True, "already_authorized": True}
        if result.status == "flood_wait":
            await _systemctl("start", _TELETHON_UNIT)
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                                detail=f"Telegram flood-wait — retry in {result.retry_after_s}s",
                                headers={"Retry-After": str(result.retry_after_s or 60)})
        if result.status == "bad_phone":
            await _systemctl("start", _TELETHON_UNIT)
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="invalid phone number")
        if result.status != "code_sent" or not result.phone_code_hash:
            await _systemctl("start", _TELETHON_UNIT)
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR,
                                detail=f"unexpected auth state: {result.status}")
        # unit stays STOPPED between send-code and sign-in — see cancel below
        return {"ok": True, "phone_code_hash": result.phone_code_hash}

    @app.post("/api/setup/telegram/sign-in")
    async def setup_telegram_sign_in(body: TelegramSignInIn, request: Request,
                                     role: str = Depends(auth.verify)):
        """Finish the Telegram sign-in with the code (+ 2FA password if the
        account has one). Success restarts the worker; need_2fa keeps it
        stopped and the wizard shows the password field."""
        if role != "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="admin only")
        env = {"AUTH_CODE": body.code.strip(),
               "AUTH_PHONE_CODE_HASH": body.phone_code_hash.strip()}
        if body.phone:
            env["AUTH_PHONE"] = body.phone.strip()
        if body.password:
            env["AUTH_PASSWORD"] = body.password
        result = await _telethon_auth_cmd("auth-sign-in", env)
        if result.status == "signed_in":
            async with request.app.state.pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO memory.source_status (source_key, status, reason, updated_at)
                    VALUES ('telegram', 'ok', NULL, now())
                    ON CONFLICT (source_key) DO UPDATE
                       SET status = 'ok', reason = NULL, updated_at = now()
                    """)
            await _systemctl("start", _TELETHON_UNIT)
            return {"ok": True, "user": result.user}
        if result.status == "need_2fa":
            return {"ok": False, "need_2fa": True}   # unit stays stopped
        if result.status == "flood_wait":
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                                detail=f"Telegram flood-wait — retry in {result.retry_after_s}s",
                                headers={"Retry-After": str(result.retry_after_s or 60)})
        # bad_code / code_expired / bad_password: unit stays stopped for retry
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=result.status)

    @app.post("/api/setup/telegram/cancel")
    async def setup_telegram_cancel(role: str = Depends(auth.verify)):
        """Abandon-safety: restart the live worker after a cancelled/failed
        wizard flow. (A browser dying mid-flow leaves the unit stopped — the
        health page and OnFailure alerts surface that; this is the fix.)"""
        if role != "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="admin only")
        await _systemctl("start", _TELETHON_UNIT)
        return {"ok": True}

    @app.post("/api/setup/secrets/{source_key}")
    async def setup_put_secret(source_key: str, body: SourceSecretIn, request: Request,
                               role: str = Depends(auth.verify)):
        """Store a per-source secret from the wizard (v1: the Granola API key).
        Write-only — the value is never echoed back to a browser."""
        if role != "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="admin only")
        if source_key not in setup_flow.SECRET_SOURCES:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="unknown secret source")
        secret_value = body.secret.strip()
        if not secret_value:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="empty secret")
        async with request.app.state.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO memory.source_secret (source_key, secret, updated_at)
                VALUES ($1, $2, now())
                ON CONFLICT (source_key) DO UPDATE
                   SET secret = EXCLUDED.secret, updated_at = now()
                """, source_key, secret_value)
        return {"ok": True, "configured": True}

    @app.get("/api/setup/secrets/{source_key}")
    async def setup_secret_status(source_key: str, request: Request,
                                  role: str = Depends(auth.verify)):
        if role != "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="admin only")
        if source_key not in setup_flow.SECRET_SOURCES:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="unknown secret source")
        async with request.app.state.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT updated_at FROM memory.source_secret WHERE source_key = $1",
                source_key)
        return {"configured": row is not None,
                "updated_at": row["updated_at"].isoformat() if row else None}

    @app.get("/api/internal/source-secret/{source_key}")
    async def internal_source_secret(source_key: str, request: Request):
        """Machine channel: a DB-less worker (Granola) fetches its key here at
        run start. BEARER-HEADER-ONLY on purpose — a browser session cookie or
        Authelia identity must never retrieve raw secrets."""
        cfg = request.app.state.cfg
        header = request.headers.get("authorization") or ""
        token = header[7:].strip() if header.lower().startswith("bearer ") else None
        if auth.role_for_token(cfg, token) != "admin":
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="bearer token required")
        if source_key not in setup_flow.SECRET_SOURCES:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="unknown secret source")
        async with request.app.state.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT secret FROM memory.source_secret WHERE source_key = $1",
                source_key)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="not configured")
        return {"secret": row["secret"]}

    @app.post("/api/setup/linkedin/upload")
    async def setup_linkedin_upload(request: Request, file: UploadFile = File(...),
                                    role: str = Depends(auth.verify)):
        """Upload the LinkedIn data export (ZIP, or a bare Connections.csv that
        gets wrapped) and run the importer on it. Streams to disk with a size
        cap — never buffers the whole body."""
        if role != "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="admin only")
        if not setup_flow.valid_upload_name(file.filename):
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                detail="upload a .zip (LinkedIn export) or .csv")
        os.makedirs(_IMPORTS_DIR, exist_ok=True)
        ts = int(datetime.now().timestamp())
        is_csv = file.filename.lower().endswith(".csv")
        dest = os.path.join(_IMPORTS_DIR, f"linkedin-{ts}.zip")
        try:
            if is_csv:
                # wrap the bare CSV into the zip layout the importer expects
                csv_tmp = os.path.join(_IMPORTS_DIR, f"linkedin-{ts}-connections.csv")
                written = 0
                with open(csv_tmp, "wb") as f:
                    while chunk := await file.read(1 << 20):
                        written += len(chunk)
                        if written > setup_flow.MAX_UPLOAD_BYTES:
                            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                                                detail="upload too large")
                        f.write(chunk)
                import zipfile
                with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
                    zf.write(csv_tmp, "Connections.csv")
                os.unlink(csv_tmp)
            else:
                written = 0
                with open(dest, "wb") as f:
                    while chunk := await file.read(1 << 20):
                        written += len(chunk)
                        if written > setup_flow.MAX_UPLOAD_BYTES:
                            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                                                detail="upload too large")
                        f.write(chunk)
        except HTTPException:
            for p in (dest, os.path.join(_IMPORTS_DIR, f"linkedin-{ts}-connections.csv")):
                if os.path.exists(p):
                    os.unlink(p)
            raise

        proc = await asyncio.create_subprocess_exec(
            _LINKEDIN_PY, "-m", "linkedin_import", dest,
            cwd=_LINKEDIN_CWD, env=os.environ.copy(),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
        except asyncio.TimeoutError:
            proc.kill()
            raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT,
                                detail="import timed out after 5 min")
        out_text = (stdout or b"").decode("utf-8", errors="replace")
        if proc.returncode != 0:
            tail = (stderr or b"").decode("utf-8", errors="replace")[-400:]
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR,
                                detail=f"import exit {proc.returncode}: {tail}")
        counts = setup_flow.parse_linkedin_output(out_text)
        return {"ok": True, "counts": counts, "log_tail": out_text[-400:]}

    # --- auth ----------------------------------------------------------

    @app.post("/api/login")
    async def login(body: LoginIn, request: Request, response: Response):
        cfg = request.app.state.cfg
        role = auth.role_for_token(cfg, body.token)
        if role is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="bad token")
        # SameSite=Lax (not Strict): the session must survive the top-level GET
        # that lands the user back on a page after an external round-trip — e.g.
        # the Google OAuth reconnect returns to /sources, and a Strict cookie is
        # withheld across that cross-site redirect chain, bouncing the user to
        # /login. Lax still blocks the cross-site POST/subresource CSRF vectors,
        # and the API re-validates the token on every request regardless.
        response.set_cookie(
            key=cfg.cookie_name, value=body.token, httponly=True, secure=True,
            samesite="lax", max_age=60 * 60 * 24 * 30, path="/",  # 30 days
        )
        # readable companion cookie — UX routing only (the API enforces the real
        # boundary); lets the Next middleware + app-shell gate by role.
        response.set_cookie(
            key="merge_role", value=role, httponly=False, secure=True,
            samesite="lax", max_age=60 * 60 * 24 * 30, path="/",
        )
        return {"ok": True, "role": role}

    @app.post("/api/logout")
    async def logout(request: Request, response: Response):
        response.delete_cookie(request.app.state.cfg.cookie_name, path="/")
        response.delete_cookie("merge_role", path="/")
        return {"ok": True}

    @app.get("/api/me")
    async def me(request: Request, role: str = Depends(auth.verify)):
        """The caller's role, for the UI to gate navigation. Source of truth now
        that humans authenticate via Authelia (which doesn't set the merge_role
        cookie) — verify() resolves the role from the Authelia identity or token."""
        return {"role": role}

    # --- persons -------------------------------------------------------

    @app.get("/api/persons", response_model=list[PersonRow])
    async def list_persons_route(
        request: Request,
        q: str | None = None,
        company_id: str | None = None,
        circle: str | None = None,
        limit: int = 50,
        offset: int = 0,
        _: None = Depends(auth.verify),
    ):
        """?circle= takes a circle KEY ('family'). /circles only ever showed who
        was overdue; this is how you see everyone in one, cadence or not."""
        pool = request.app.state.pool
        limit = max(1, min(limit, 200))
        viewer = await auth.resolve_viewer(request)   # member → shared + own contacts only
        rows = await queries.list_persons(
            pool, q=q, company_id=company_id, circle=circle,
            limit=limit, offset=offset, viewer=viewer,
        )
        return rows

    @app.get("/api/persons/count")
    async def count_persons_route(
        request: Request,
        q: str | None = None,
        company_id: str | None = None,
        circle: str | None = None,
        _: None = Depends(auth.verify),
    ):
        """Total live persons matching the optional ?q= name + ?company_id= +
        ?circle= filters. Registered BEFORE /api/persons/{person_id} so the
        literal 'count' path segment isn't shadowed by the UUID parameter route."""
        pool = request.app.state.pool
        viewer = await auth.resolve_viewer(request)
        return {"count": await queries.count_persons(
            pool, q=q, company_id=company_id, circle=circle, viewer=viewer)}

    # --- contact circles ------------------------------------------------

    @app.get("/api/circles", response_model=list[ContactCircle])
    async def list_circles_route(request: Request, _: None = Depends(auth.verify)):
        return await queries.list_circles(request.app.state.pool)

    @app.post("/api/circles", response_model=ContactCircle)
    async def create_circle_route(
        body: ContactCircleIn, request: Request, _: None = Depends(auth.verify),
    ):
        try:
            return await queries.create_circle(request.app.state.pool, body.model_dump())
        except Exception as e:  # noqa: BLE001 — unique key is the expected failure
            if "unique" in str(e).lower():
                raise HTTPException(status.HTTP_409_CONFLICT,
                                    detail=f"a circle with key '{body.key}' already exists")
            raise

    @app.patch("/api/circles/{circle_id}", response_model=ContactCircle)
    async def patch_circle_route(
        circle_id: str, body: ContactCirclePatch, request: Request,
        _: None = Depends(auth.verify),
    ):
        row = await queries.patch_circle(
            request.app.state.pool, circle_id, body.model_dump(exclude_unset=True))
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="circle not found")
        return row

    @app.delete("/api/circles/{circle_id}", status_code=204)
    async def delete_circle_route(
        circle_id: str, request: Request, _: None = Depends(auth.verify),
    ):
        if not await queries.delete_circle(request.app.state.pool, circle_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="circle not found")
        return None

    @app.get("/api/circles/due", response_model=list[CircleDueRow])
    async def circles_due_route(
        request: Request, limit: int = 100, _: None = Depends(auth.verify),
    ):
        """Contacts you're overdue to speak to, per their circle's cadence.
        Judged by the STRICTEST circle they belong to; ranked by that circle's
        priority, then by how far overdue. No GET /circles/{id} route exists,
        so the literal 'due' path can't be shadowed."""
        return await queries.circles_due(request.app.state.pool, limit=limit)

    # --- reconnect follow-ups -------------------------------------------

    @app.get("/api/followups", response_model=list[FollowupRow])
    async def list_followups_route(
        request: Request, scope: str = "open", person_id: str | None = None,
        limit: int = 200, _: None = Depends(auth.verify),
    ):
        """The reconnect pipeline: conversations you owe people, before any of
        it is a deal. ?scope=open (default) is what's still owed; ?scope=all
        includes the ones that have since happened.

        Whether one happened is derived from canonical.interaction — talk to
        them on Telegram or email and it settles itself, no worker involved."""
        return await queries.list_followups(
            request.app.state.pool, scope=scope, person_id=person_id, limit=limit)

    @app.post("/api/followups", response_model=FollowupRow, status_code=201)
    async def create_followup_route(
        body: FollowupCreate, request: Request, _: None = Depends(auth.verify),
    ):
        row = await queries.create_followup(
            request.app.state.pool, person_id=body.person_id,
            due_date=body.due_date, due_time=body.due_time, topic=body.topic)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="person not found")
        return row

    # DECLARED BEFORE /api/followups/{followup_id}. FastAPI matches routes in
    # declaration order, so the other way round 'bulk' would bind as a
    # followup_id and 500 on the uuid cast.
    @app.patch("/api/followups/bulk", response_model=FollowupBulkResult)
    async def bulk_patch_followups_route(
        body: FollowupBulkPatch, request: Request, _: None = Depends(auth.verify),
    ):
        """Reschedule or settle a whole selection at once. Follow-ups planned
        in one sitting share a due date, and moving them one at a time is how
        a long list stops being worth keeping."""
        changed = await queries.bulk_patch_followups(
            request.app.state.pool, body.ids,
            body.model_dump(exclude_unset=True, exclude={"ids"}))
        return {"changed": changed}

    @app.post("/api/followups/bulk/delete", response_model=FollowupBulkResult)
    async def bulk_delete_followups_route(
        body: FollowupBulkIds, request: Request, _: None = Depends(auth.verify),
    ):
        """POST, not DELETE: the ids travel in a body, and a DELETE with a body
        is the kind of thing proxies quietly drop. Soft-delete, like the
        single-row route."""
        changed = await queries.bulk_delete_followups(request.app.state.pool, body.ids)
        return {"changed": changed}

    @app.patch("/api/followups/{followup_id}", response_model=FollowupRow)
    async def patch_followup_route(
        followup_id: str, body: FollowupPatch, request: Request,
        _: None = Depends(auth.verify),
    ):
        row = await queries.patch_followup(
            request.app.state.pool, followup_id, body.model_dump(exclude_unset=True))
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="follow-up not found")
        return row

    @app.post("/api/followups/{followup_id}/notes", response_model=FollowupRow)
    async def add_followup_note_route(
        followup_id: str, body: FollowupNoteIn, request: Request,
        _: None = Depends(auth.verify),
    ):
        """Log what was discussed, or what changed. Returns the whole
        follow-up so the caller re-renders from one response."""
        row = await queries.add_followup_note(request.app.state.pool, followup_id, body.body)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="follow-up not found")
        return row

    @app.delete("/api/followups/{followup_id}/notes/{note_id}", response_model=FollowupRow)
    async def delete_followup_note_route(
        followup_id: str, note_id: str, request: Request,
        _: None = Depends(auth.verify),
    ):
        pool = request.app.state.pool
        parent = await queries.delete_followup_note(pool, note_id)
        if parent is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="note not found")
        return await queries.get_followup(pool, parent)

    @app.delete("/api/followups/{followup_id}", status_code=204)
    async def delete_followup_route(
        followup_id: str, request: Request, _: None = Depends(auth.verify),
    ):
        if not await queries.delete_followup(request.app.state.pool, followup_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="follow-up not found")
        return None

    @app.put("/api/persons/{person_id}/circles", response_model=list[ContactCircle])
    async def set_person_circles_route(
        person_id: str, body: PersonCirclesIn, request: Request,
        _: None = Depends(auth.verify),
    ):
        pool = request.app.state.pool
        await queries.set_person_circles(pool, person_id, body.circle_ids)
        return await queries.list_circles(pool)

    @app.get("/api/prospects", response_model=list[ProspectRow])
    async def list_prospects_route(
        request: Request,
        min_interactions: int = 20,
        dormant_after_days: int = 90,
        dormant_before_days: int | None = None,
        include_dismissed: bool = False,
        include_pipeline: bool = False,
        q: str | None = None,
        limit: int = 50,
        offset: int = 0,
        _: None = Depends(auth.verify),
    ):
        """Reconnect/BD hunt: contacts ranked by relationship depth × how cold
        they've gone (see queries.PROSPECTS_SQL). Owner-only — the middleware
        allow-list (_budget_api_allowed) already 403s budget members here."""
        return await queries.list_prospects(
            request.app.state.pool,
            min_interactions=min_interactions, dormant_after_days=dormant_after_days,
            dormant_before_days=dormant_before_days, include_dismissed=include_dismissed,
            include_pipeline=include_pipeline, q=q, limit=limit, offset=offset,
        )

    async def _embed_query(cfg, text: str) -> list[float] | None:
        """Encode `text` via the MCP server's e5-base embed endpoint (same model
        that built memory.profile.embedding). None on any failure → the caller
        surfaces 503; reconnect ranking never depends on this."""
        if not cfg.mcp_bearer:
            return None
        import httpx
        try:
            async with httpx.AsyncClient(timeout=30) as hc:
                r = await hc.post(cfg.mcp_embed_url, json={"query": text},
                                  headers={"Authorization": f"Bearer {cfg.mcp_bearer}"})
                r.raise_for_status()
                vec = r.json().get("vector")
                return vec if isinstance(vec, list) and vec else None
        except Exception:  # noqa: BLE001
            log.exception("prospect ICP embed failed")
            return None

    @app.get("/api/prospects/search", response_model=list[ProspectRow])
    async def search_prospects_route(
        request: Request,
        q: str,
        min_interactions: int = 20,
        dormant_after_days: int = 90,
        dormant_before_days: int | None = None,
        include_dismissed: bool = False,
        include_pipeline: bool = False,
        limit: int = 50,
        offset: int = 0,
        _: None = Depends(auth.verify),
    ):
        """ICP-fit prospecting: rank the reconnect-eligible pool by semantic
        closeness to a free-text ICP description ('crypto fund partner',
        'connector who makes introductions'). Owner-only via the middleware
        allow-list. Only contacts with a built profile embedding are searchable."""
        if not q.strip():
            return []
        qvec = await _embed_query(request.app.state.cfg, q.strip())
        if qvec is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                detail="semantic search unavailable — the embed service is down")
        return await queries.search_prospects(
            request.app.state.pool, qvec=qvec,
            min_interactions=min_interactions, dormant_after_days=dormant_after_days,
            dormant_before_days=dormant_before_days, include_dismissed=include_dismissed,
            include_pipeline=include_pipeline, limit=limit, offset=offset,
        )

    @app.post("/api/prospects/{person_id}/dismiss", status_code=204)
    async def dismiss_prospect_route(
        person_id: str, request: Request, _: None = Depends(auth.verify),
    ):
        await queries.set_prospect_dismissed(request.app.state.pool, person_id, dismissed=True)
        return None

    @app.delete("/api/prospects/{person_id}/dismiss", status_code=204)
    async def undismiss_prospect_route(
        person_id: str, request: Request, _: None = Depends(auth.verify),
    ):
        await queries.set_prospect_dismissed(request.app.state.pool, person_id, dismissed=False)
        return None

    @app.get("/api/persons/{person_id}", response_model=PersonDetail)
    async def get_person_route(
        person_id: str, request: Request, _: None = Depends(auth.verify),
    ):
        pool = request.app.state.pool
        viewer = await auth.resolve_viewer(request)
        if not await queries.member_can_see(pool, viewer, person_id, table="canonical.person"):
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="person not found")
        row = await queries.get_person(pool, person_id)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="person not found")
        return row

    @app.patch("/api/persons/{person_id}/sharing", response_model=PersonDetail)
    async def set_person_sharing_route(
        person_id: str, body: ContactSharing, request: Request, role: str = Depends(auth.verify),
    ):
        """Owner action: share/unshare a contact (and optionally assign its owner)."""
        if role != "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="owner only")
        pool = request.app.state.pool
        ok = await queries.set_person_sharing(
            pool, person_id, visibility=body.visibility, owner_member_id=body.owner_member_id)
        if not ok:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="person not found")
        return await queries.get_person(pool, person_id)

    @app.patch("/api/persons/{person_id}/sensitivity", response_model=PersonDetail)
    async def set_person_sensitivity_route(
        person_id: str, body: PersonSensitivity, request: Request,
        role: str = Depends(auth.verify),
    ):
        """Sensitivity-routing opt-in (owner action): a sensitive contact's
        messages are only ever processed by the local LLM — profile builder,
        interaction scanner, and draft outreach all route to Ollama for them."""
        if role != "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="owner only")
        pool = request.app.state.pool
        ok = await queries.set_person_sensitive(pool, person_id, sensitive=body.sensitive)
        if not ok:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="person not found")
        return await queries.get_person(pool, person_id)

    @app.post("/api/persons/{person_id}/identities", response_model=IdentityRow)
    async def add_identity_route(
        person_id: str, body: IdentityCreate, request: Request,
        _: None = Depends(auth.verify),
    ):
        pool = request.app.state.pool
        row = await queries.add_identity(pool, person_id, body.source, body.source_id)
        if row is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail="identity already exists (may belong to another person)",
            )
        # Enrich in the SAME action: a LinkedIn vanity we already hold CSV data
        # for gets its role/company/connect-date attached immediately, instead of
        # sitting as a bare link next to data we own. Never fatal — the identity
        # is already saved and a failure here just means no extra detail.
        if body.source == "linkedin":
            try:
                ev = await queries.enrich_linkedin_identity(pool, person_id, body.source_id.strip())
                if ev:
                    row = {**row, "evidence": ev}
            except Exception:  # noqa: BLE001
                log.exception("linkedin enrich failed for %s", body.source_id)
        return row

    @app.patch("/api/persons/{person_id}/identities/{identity_id}", response_model=IdentityRow)
    async def patch_identity_role_route(
        person_id: str, identity_id: int, body: IdentityRolePatch, request: Request,
        _: None = Depends(auth.verify),
    ):
        """Set role/company on an identity you added by hand. Stored in the same
        `evidence` shape the LinkedIn importer writes, so the profile builder,
        the LinkedIn card and the authoritative-title prompt rule all treat it
        identically — and, since evidence feeds the profile input_sig, the
        summary is queued for rebuild automatically."""
        fields = body.model_dump(exclude_unset=True)
        row = await queries.set_identity_role(
            request.app.state.pool, person_id, identity_id,
            position=fields.get("position"), company=fields.get("company"),
        )
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="identity not found on this person")
        return row

    @app.delete("/api/persons/{person_id}/identities/{identity_id}", status_code=204)
    async def remove_identity_route(
        person_id: str, identity_id: int, request: Request,
        _: None = Depends(auth.verify),
    ):
        pool = request.app.state.pool
        removed = await queries.remove_identity(pool, person_id, identity_id)
        if not removed:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="identity not found on this person")
        return None

    # --- LinkedIn capture (browser extension) --------------------------

    @app.get("/api/capture/linkedin/{vanity}", response_model=LinkedInCaptureLookup)
    async def linkedin_capture_lookup_route(
        vanity: str, request: Request, _: None = Depends(auth.verify),
    ):
        """Do we already know this profile? The extension asks before showing
        its form, so the button reads 'Update' (with a link to the person)
        instead of 'Add' — and you don't create a second card for someone whose
        vanity arrived with the CSV import years ago."""
        norm = queries.normalize_linkedin_vanity(vanity)
        if not norm:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="unparseable linkedin vanity")
        found = await queries.find_person_by_linkedin_vanity(request.app.state.pool, norm)
        if not found or not found.get("person_id"):
            return LinkedInCaptureLookup(
                vanity=norm, known=False,
                last_captured_at=(found or {}).get("last_captured_at"))
        return LinkedInCaptureLookup(
            vanity=norm, known=True, person_id=found["person_id"],
            display_name=found["display_name"],
            last_captured_at=found.get("last_captured_at"))

    @app.post("/api/capture/linkedin", response_model=LinkedInCaptureResult)
    async def linkedin_capture_route(
        body: LinkedInCapture, request: Request, _: None = Depends(auth.verify),
    ):
        """Add (or refresh) a contact from the LinkedIn profile page you're on.

        LinkedIn can't be fetched server-side, so the browser extension reads
        the open tab and posts what it saw. Lands in raw.linkedin_profile_capture
        and folds into canonical the same way the CSV import does — a captured
        contact is indistinguishable downstream from an imported one.

        Idempotent on the vanity: capturing twice refreshes, never duplicates.
        """
        payload = body.model_dump()
        payload["experience"] = [e for e in payload.get("experience") or [] if any(e.values())]
        payload["education"] = [e for e in payload.get("education") or [] if any(e.values())]
        try:
            result = await queries.capture_linkedin_profile(request.app.state.pool, payload)
        except ValueError as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))
        return LinkedInCaptureResult(**result)

    # --- name hygiene --------------------------------------------------

    @app.get("/api/persons/{person_id}/name-suggestions")
    async def name_suggestions_route(
        person_id: str, request: Request, _: None = Depends(auth.verify),
    ):
        """Candidate display names pulled from raw.linkedin_*, raw.google_contact,
        and raw.telegram_user. Always returns:
          {
            "current_display_name": str,
            "current_is_synthetic": bool,
            "suggestions": [{source, suggested, evidence}, ...]
          }
        The UI shows the banner only when current_is_synthetic AND
        suggestions is non-empty, but it still returns both fields so a
        future 'always show alternates' surface can opt in."""
        pool = request.app.state.pool
        me = await queries.get_person(pool, person_id)
        if me is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="person not found")
        suggestions = await queries.list_name_suggestions(
            pool, person_id, me["display_name"],
        )
        return {
            "current_display_name": me["display_name"],
            "current_is_synthetic": queries.is_synthetic_display_name(me["display_name"]),
            "suggestions": suggestions,
        }

    @app.post("/api/persons/{person_id}/delete")
    async def soft_delete_person_route(
        person_id: str, request: Request, _: None = Depends(auth.verify),
    ):
        """Soft-delete: sets canonical.person.deleted_at = now(). The
        row + identities + profile + photo stay intact so /restore is
        a single UPDATE. All live queries filter `deleted_at IS NULL`."""
        pool = request.app.state.pool
        ok = await queries.soft_delete_person(pool, person_id)
        if not ok:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="person not found (or already deleted/merged)")
        return {"ok": True}

    @app.post("/api/persons/{person_id}/restore")
    async def restore_person_route(
        person_id: str, request: Request, _: None = Depends(auth.verify),
    ):
        """Undo a soft-delete."""
        pool = request.app.state.pool
        ok = await queries.restore_person(pool, person_id)
        if not ok:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="person not in deleted state")
        return {"ok": True}

    @app.get("/api/cleanup/candidates")
    async def cleanup_candidates_route(
        request: Request,
        q: str | None = None,
        limit: int = 50,
        offset: int = 0,
        _: None = Depends(auth.verify),
    ):
        """Persons that look like automation senders (newsletters, noreply,
        brand bots). Sorted busiest-first so the top of the list is the
        most-spammy in absolute message volume."""
        pool = request.app.state.pool
        limit = max(1, min(limit, 200))
        return await queries.list_cleanup_candidates(pool, q=q, limit=limit, offset=offset)

    @app.get("/api/cleanup/count")
    async def cleanup_count_route(
        request: Request, q: str | None = None, _: None = Depends(auth.verify),
    ):
        pool = request.app.state.pool
        return {"count": await queries.count_cleanup_candidates(pool, q=q)}

    @app.post("/api/cleanup/delete-batch")
    async def cleanup_delete_batch_route(
        request: Request, _: None = Depends(auth.verify),
    ):
        """Bulk soft-delete. Body: {ids: ["<uuid>", ...]}. Returns the
        actually-deleted count (already-deleted/merged ids are skipped)."""
        pool = request.app.state.pool
        body = await request.json()
        ids = body.get("ids") or []
        if not isinstance(ids, list) or not all(isinstance(x, str) for x in ids):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="`ids` must be a list of UUID strings")
        if len(ids) > 1000:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="batch capped at 1000 ids per call")
        n = await queries.soft_delete_persons(pool, ids)
        return {"deleted": n, "requested": len(ids)}

    @app.post("/api/persons/{person_id}/rename")
    async def rename_person_route(
        person_id: str, body: RenameIn, request: Request,
        _: None = Depends(auth.verify),
    ):
        """Sets canonical.person.display_name. Idempotent."""
        pool = request.app.state.pool
        ok = await queries.rename_person(pool, person_id, body.display_name)
        if not ok:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="person not found (or merged away)")
        return {"ok": True, "display_name": body.display_name.strip()}

    @app.put("/api/persons/{person_id}/birthday")
    async def set_person_birthday_route(
        person_id: str, body: BirthdayIn, request: Request,
        _: None = Depends(auth.verify),
    ):
        """Set or clear the manual override birthday on a person. Accepts
        'YYYY-MM-DD' or 'MM-DD' (year → 1900 sentinel); null/empty clears it."""
        bday = _parse_birthday(body.birthday)
        ok = await queries.set_person_birthday(request.app.state.pool, person_id, bday)
        if not ok:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="person not found (or merged away)")
        return {"ok": True, "birthday": bday.isoformat() if bday else None}

    # --- avatars -------------------------------------------------------

    @app.get("/api/persons/{person_id}/photo")
    async def person_photo_route(
        person_id: str, request: Request, _: None = Depends(auth.verify),
    ):
        """Serves a profile photo for a person, falling through a precedence:
          1. memory.person_photo with local_path → stream the file (telegram)
          2. memory.person_photo with url        → 307 redirect (google_contacts)
          3. fallback to Gravatar by first email identity (d=404 → real 404
             if no gravatar, which lets the browser's onError trigger the
             initials fallback in the UI)
          4. 404
        Cache for 1 hour so the virtualized persons list isn't re-fetching
        the same avatar on every scroll past."""
        pool = request.app.state.pool
        row = await queries.get_person_photo(pool, person_id)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="person not found")

        cache_headers = {"Cache-Control": "private, max-age=3600"}

        local_path = row.get("photo_local_path")
        if local_path:
            # Defensive: only serve from inside the configured avatars dir
            # so a bad row can't make us stream /etc/passwd.
            cfg = request.app.state.cfg
            real = os.path.realpath(local_path)
            base = os.path.realpath(cfg.avatars_dir)
            if real.startswith(base + os.sep) and os.path.isfile(real):
                return FileResponse(real, media_type="image/jpeg", headers=cache_headers)

        url = row.get("photo_url")
        if url:
            return RedirectResponse(url, status_code=307, headers=cache_headers)

        email = row.get("first_email")
        if email:
            digest = hashlib.md5(email.strip().lower().encode("utf-8")).hexdigest()
            # d=404 → Gravatar 404s when there's no image, so the browser's
            # <img onError> trips and the UI shows initials. s=200 is the
            # 2x size of our largest avatar (h-16 ≈ 64px) for retina.
            gravatar = f"https://www.gravatar.com/avatar/{digest}?d=404&s=200"
            return RedirectResponse(gravatar, status_code=307, headers=cache_headers)

        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no photo")

    # --- merge queue ---------------------------------------------------

    @app.get("/api/persons/{person_id}/similar")
    async def list_similar_route(
        person_id: str, request: Request, _: None = Depends(auth.verify),
    ):
        """Other active canonical.persons whose display_name matches.
        Catches duplicates that no generator emitted (e.g. the fuzzy_name
        cap dropped them, or LinkedIn vanity + telegram with no bridge)."""
        pool = request.app.state.pool
        me = await queries.get_person(pool, person_id)
        if me is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="person not found")
        return await queries.list_similar_persons(pool, person_id, me["display_name"])

    @app.post("/api/persons/{person_id}/similar/{other_id}/dismiss", status_code=204)
    async def dismiss_similar_route(
        person_id: str, other_id: str, request: Request,
        _: None = Depends(auth.verify),
    ):
        """Stop suggesting (this person, other_id) as a potential merge.
        Writes a rejected merge_candidate row; the similar-persons query
        filters those out."""
        pool = request.app.state.pool
        if person_id == other_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="cannot dismiss self")
        await queries.dismiss_similar_pair(pool, person_id, other_id)
        return None

    @app.post("/api/persons/{person_id}/merge-with/{other_id}")
    async def direct_merge_route(
        person_id: str, other_id: str, request: Request,
        winner: str = "this",   # 'this' = person_id wins; 'other' = other_id wins
        _: None = Depends(auth.verify),
    ):
        """Execute a merge directly between two persons, without going
        through the merge_candidate queue. Used by the 'Other people with
        this name' UI on the person detail page."""
        pool = request.app.state.pool
        if winner not in ("this", "other"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="winner must be 'this' or 'other'")
        if person_id == other_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="cannot merge with self")
        winner_id = person_id if winner == "this" else other_id
        loser_id  = other_id  if winner == "this" else person_id
        note = f"direct merge via UI (winner={winner})"
        await queries.execute_merge(pool, winner_id, loser_id, note)
        return {"ok": True, "winner_id": winner_id, "loser_id": loser_id}

    # --- telegram group allowlist --------------------------------------

    # --- Granola ingest + suggestions inbox (Phase 2) ------------------

    @app.post("/api/ingest/granola")
    async def ingest_granola_route(
        body: GranolaIngest, request: Request, _: None = Depends(auth.verify),
    ):
        """Ingest one meeting recap → store it + Haiku-extract → write
        pending suggestions (tasks, opportunities, person-mentions).
        Idempotent on source_id: re-ingesting refreshes the recap and
        replaces its *pending* suggestions (accepted/dismissed kept)."""
        pool = request.app.state.pool
        client = request.app.state.anthropic
        if client is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="extraction unavailable — ANTHROPIC_API_KEY not configured",
            )
        text = body.summary or body.transcript
        if not text or not text.strip():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="summary or transcript required")

        # Cheap idempotency for recurring polls: skip meetings already
        # processed unless explicitly forced. The cron can re-send its
        # whole recent window every run and only new meetings cost a
        # Haiku call. BUT an *edited* note (Granola finished/changed its
        # summary after we first ingested) has the same source_id with new
        # content — re-extract those by comparing the stored summary to the
        # incoming one, so summary edits flow through to fresh suggestions.
        if not body.force:
            existing = await queries.get_meeting_recap_by_source(pool, "granola", body.source_id)
            if existing and existing.get("processed_at") and (existing.get("summary") or "") == text:
                return {"ok": True, "skipped": "already_processed", "recap_id": existing["id"]}
            reingest = bool(existing and existing.get("processed_at"))
        else:
            reingest = False

        cfg = request.app.state.cfg
        projects = await queries.list_projects(pool, status=None)
        attendees = [a.model_dump() for a in body.attendees]

        try:
            extracted = await extraction.extract_recap(
                client, cfg.extraction_model,
                title=body.title,
                meeting_date=body.meeting_date.isoformat() if body.meeting_date else None,
                attendees=attendees, summary=text, projects=projects,
            )
        except Exception as e:
            log.exception("granola extraction failed for %s", body.source_id)
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"extraction error: {e}")
        if extracted is None:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="model returned no extraction")

        # Resolve inferred project slug → id
        slug = extracted.get("project_slug")
        project_id = None
        if slug:
            for p in projects:
                if p["slug"] == slug:
                    project_id = p["id"]
                    break

        recap = await queries.upsert_meeting_recap(
            pool,
            source="granola", source_id=body.source_id, title=body.title,
            meeting_date=body.meeting_date, project_id=project_id,
            summary=text, recap=extracted.get("recap"), attendees=attendees,
        )
        recap_id = recap["id"]
        await queries.delete_suggestions_for_source(pool, recap_id)

        async def _resolve(name: str | None):
            if not name or not name.strip():
                return None, None, "medium"
            m = await queries.resolve_person_by_name(pool, name.strip())
            if not m:
                return None, name.strip(), "low"
            return m["person_id"], name.strip(), ("high" if m["exact"] else "medium")

        counts = {"task": 0, "opportunity": 0, "person_mention": 0, "deduped": 0}
        linked_person_names: set[str] = set()
        dedup_threshold = request.app.state.cfg.suggestion_dedup_similarity

        # Action items → task suggestions
        for ai in extracted.get("action_items") or []:
            title = (ai.get("title") or "").strip()
            if not title:
                continue
            with_person = (ai.get("with_person") or "").strip()
            pid, raw, conf = await _resolve(with_person)
            if raw:
                linked_person_names.add(raw.lower())
            # Cross-source dedup: skip if a Telegram-scanner (or prior) suggestion
            # for the same person already covers this.
            if await queries.similar_item_exists(pool, pid, title, dedup_threshold):
                counts["deduped"] += 1
                continue
            detail_bits = []
            if ai.get("owner"):
                detail_bits.append(f"Owner: {ai['owner']}")
            if ai.get("due_hint"):
                detail_bits.append(f"Timing: {ai['due_hint']}")
            await queries.create_suggestion(pool, {
                "kind": "task", "title": title,
                "detail": " · ".join(detail_bits) or None,
                "project_id": project_id,
                "person_id": pid, "person_name_raw": raw,
                "owner_hint": ai.get("owner") or None,
                "confidence": conf, "source_kind": "granola", "source_ref": recap_id,
                "context_at": body.meeting_date,
            })
            counts["task"] += 1

        # Opportunities → opportunity suggestions
        for op in extracted.get("opportunities") or []:
            title = (op.get("title") or "").strip()
            if not title:
                continue
            cparty = (op.get("counterparty") or "").strip()
            pid, raw, conf = await _resolve(cparty)
            if raw:
                linked_person_names.add(raw.lower())
            if await queries.similar_item_exists(pool, pid, title, dedup_threshold):
                counts["deduped"] += 1
                continue
            await queries.create_suggestion(pool, {
                "kind": "opportunity", "title": title,
                "detail": None,
                "project_id": project_id,
                "person_id": pid, "person_name_raw": raw,
                "suggested_stage": op.get("stage") or "intro",
                "estimated_value": op.get("value") or None,
                "confidence": conf, "source_kind": "granola", "source_ref": recap_id,
                "context_at": body.meeting_date,
            })
            counts["opportunity"] += 1

        # People mentioned but NOT already attached to a task/opp → a
        # "follow up with X" person_mention suggestion. Two gates (the
        # "suppress when no reason" policy): the model must have given a
        # concrete reason, AND the name must resolve to a real DB contact.
        for pm in extracted.get("people_mentioned") or []:
            # Tolerate both the new {name, reason} shape and a legacy bare
            # string (older extractions / model drift).
            if isinstance(pm, str):
                name, reason = pm.strip(), ""
            else:
                name = (pm.get("name") or "").strip()
                reason = (pm.get("reason") or "").strip()
            if not name or name.lower() in linked_person_names:
                continue
            # Suppress reasonless mentions — a bare "was mentioned" is noise.
            if not _is_substantive_reason(reason):
                continue
            m = await queries.resolve_person_by_name(pool, name)
            if not m:
                continue  # not in DB → skip (no actionable contact)
            linked_person_names.add(name.lower())
            pm_title = f"Follow up with {m['display_name']}"
            if await queries.similar_item_exists(pool, m["person_id"], pm_title, dedup_threshold):
                counts["deduped"] += 1
                continue
            await queries.create_suggestion(pool, {
                "kind": "person_mention",
                "title": f"Follow up with {m['display_name']}",
                "detail": reason,
                "project_id": project_id,
                "person_id": m["person_id"], "person_name_raw": name,
                "confidence": "high" if m["exact"] else "medium",
                "source_kind": "granola", "source_ref": recap_id,
                "context_at": body.meeting_date,
            })
            counts["person_mention"] += 1

        return {
            "ok": True,
            "recap_id": recap_id,
            "reingested": reingest,
            "project_id": project_id,
            "project_slug": slug,
            "recap": extracted.get("recap"),
            "suggested": counts,
        }

    # --- Telegram bot capture (voice/text → task/opp/event) ---------------

    async def _capture_parse(pool, client, model: str, text: str, force_type: str | None = None,
                             image_b64: str | None = None, image_media_type: str = "image/jpeg",
                             finance_only: bool = False):
        """Classify + extract a note, then resolve project_slug→id/name and
        person_name→id, folding the resolved ids into the parsed dict. When
        `finance_only` (a household member scoped to spending), contact + project
        resolution is SKIPPED so the CRM is never exposed to them."""
        projects = await queries.list_projects(pool, status=None)
        now = datetime.now(ZoneInfo("Asia/Bangkok"))
        parsed = await extraction.classify_capture(
            client, model, text=text, now_iso=now.isoformat(timespec="minutes"),
            tz_name="Asia/Bangkok", projects=projects, force_type=force_type,
            self_label=app.state.cfg.self_label,
            image_b64=image_b64, image_media_type=image_media_type,
        )
        if parsed is None:
            return None
        parsed["project_id"] = parsed["project_name"] = None
        parsed["person_id"] = None
        parsed["person_display"] = None   # canonical contact name when matched
        parsed["person_matched"] = False
        nm = (parsed.get("person_name") or "").strip()
        if not finance_only:
            if parsed.get("project_slug"):
                for p in projects:
                    if p["slug"] == parsed["project_slug"]:
                        parsed["project_id"], parsed["project_name"] = p["id"], p["name"]
                        break
            if nm:
                m = await queries.resolve_person_fuzzy(pool, nm)
                if m:
                    parsed["person_id"] = m["person_id"]
                    parsed["person_display"] = m["display_name"]
                    parsed["person_matched"] = True
        else:
            # finance-scoped member — never surface CRM contacts/projects to them
            parsed["person_name"] = ""
            parsed["project_slug"] = None
        # Normalize task scheduling (recurrence / time / duration).
        parsed["repeat"] = parsed.get("repeat") or "none"
        rw = parsed.get("repeat_weekdays") or []
        parsed["repeat_weekdays"] = sorted({_DOW_NAME[d[:3].lower()] for d in rw
                                            if isinstance(d, str) and d[:3].lower() in _DOW_NAME})
        parsed["task_time"] = (parsed.get("task_time") or "").strip()
        dur = parsed.get("duration_min")
        parsed["duration_min"] = int(dur) if isinstance(dur, (int, float)) and dur and dur > 0 else None
        if parsed.get("type") == "tasks":
            parsed["items"] = [s.strip() for s in (parsed.get("items") or [])
                               if isinstance(s, str) and s.strip()]
        if parsed.get("type") == "event":
            parsed["conference"] = bool(parsed.get("conference"))
            _assemble_event_times(parsed)
            # Meetings get a consistent "the user <> Name" title, using the matched
            # CRM name (transcribed "Brian Kong" → real "Brian Kang") when we have it.
            who = parsed["person_display"] if parsed.get("person_matched") else nm
            if who:
                parsed["title"] = f"the user <> {who}"
        if parsed.get("type") in ("expense", "income", "transfer"):
            await _resolve_capture_transaction(pool, parsed)
        return parsed

    async def _resolve_capture_transaction(pool, parsed: dict) -> None:
        """Normalise amount/currency and resolve accounts + category for an
        expense / income / transfer capture (matched against fin_account /
        fin_category)."""
        amt = parsed.get("amount")
        parsed["amount"] = float(amt) if isinstance(amt, (int, float)) and amt else 0.0
        parsed["currency"] = (parsed.get("currency") or "THB").strip().upper() or "THB"
        parsed["payee"] = (parsed.get("payee") or "").strip()
        # Honor a model-supplied date (e.g. read off a receipt slip); else today.
        slip_date = _parse_due_date(parsed.get("txn_date"))
        parsed["txn_date"] = (slip_date or _bangkok_today()).isoformat()

        # Safety net: a move where the COUNTERPARTY (the payee — the non-user
        # side) is himself / his spouse is really a transfer, not income/spending
        # — flip it so it never inflates income. We check ONLY the payee, never
        # the title/description/FROM line: on a payment slip the sender is ALWAYS
        # the owner, so matching that would flag every slip. A
        # genuine transfer has the OTHER party = own/family (payee resolves to
        # the user's own name); a merchant payee (บางจาก, Tops…) stays an expense.
        if parsed["type"] in ("income", "expense") and finance_categorize.is_self_transfer(
            parsed.get("payee")
        ):
            hint = (parsed.get("account_hint") or "").strip()
            if parsed["type"] == "income":
                parsed["to_account_hint"] = (parsed.get("to_account_hint") or hint)
            else:
                parsed["from_account_hint"] = (parsed.get("from_account_hint") or hint)
            parsed["type"] = "transfer"
            parsed["category_hint"] = ""

        if parsed["type"] == "transfer":
            # a two-legged move: resolve both sides, no payee/category
            parsed["payee"] = ""
            parsed["category_key"] = parsed["category_label"] = None
            for side in ("from", "to"):
                parsed[f"{side}_account_id"] = parsed[f"{side}_account_name"] = None
                hint = (parsed.get(f"{side}_account_hint") or "").strip()
                if hint:
                    acc = await queries.resolve_account_fuzzy(pool, hint)
                    if acc:
                        parsed[f"{side}_account_id"] = acc["id"]
                        parsed[f"{side}_account_name"] = acc["name"]
            # A transfer's destination is the RECIPIENT's own account (named after
            # them, e.g. "Jane Bangkok"). SCB-app slips to another person often
            # mis-hint the dest as SCB (the sending app), collapsing to "SCB →
            # SCB"; when the two legs are the same account (or the dest is
            # missing) and we know the recipient, re-resolve the destination to
            # their account.
            recip = (parsed.get("person_display") or parsed.get("person_name") or "").strip()
            recip = re.sub(r"^(mr|mrs|mister|mistr|ms|miss)\.?\s+", "", recip, flags=re.I)
            same = parsed.get("to_account_id") and parsed["to_account_id"] == parsed.get("from_account_id")
            if recip and (same or not parsed.get("to_account_id")):
                acc = await queries.resolve_account_fuzzy(pool, recip.split()[0])
                if acc and acc["id"] != parsed.get("from_account_id"):
                    parsed["to_account_id"], parsed["to_account_name"] = acc["id"], acc["name"]
            return

        # account: match a stated hint, else fall back to a default
        parsed["account_id"] = parsed["account_name"] = None
        acc = None
        if (parsed.get("account_hint") or "").strip():
            acc = await queries.resolve_account_fuzzy(pool, parsed["account_hint"])
        if acc is None:
            acc = await queries.default_fin_account(pool)
        if acc:
            parsed["account_id"], parsed["account_name"] = acc["id"], acc["name"]
        # Known-merchant defaults: SCB/KBank slips carry the merchant in the memo
        # ("TOPS-C2B…"→Tops/Groceries, "BTM…"→Bread Talk/Groceries, Grab→Food
        # Delivery, "เพ็ท คลับ…"→Pet Club/Pets). Normalise the payee + set a
        # default category for a spend so the user rarely has to touch it.
        if parsed["type"] == "expense":
            m = finance_categorize.merchant_default(f"{parsed.get('payee') or ''} {parsed.get('title') or ''}")
            if m:
                parsed["payee"], parsed["category_hint"] = m[0], m[1]
        # category
        parsed["category_key"] = parsed["category_label"] = None
        if (parsed.get("category_hint") or "").strip():
            cat = await queries.resolve_category_fuzzy(pool, parsed["category_hint"], kind=parsed["type"])
            if cat:
                parsed["category_key"], parsed["category_label"] = cat["key"], cat["label"]

    async def _finalize_event(pool, cfg, capture_id: str, parsed: dict, attendee_email: str | None):
        """Insert the calendar event (optionally inviting an attendee) and mark
        the capture confirmed. Shared by the no-email confirm path and /invite."""
        if not cfg.work_calendar_account:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                detail="calendar account not configured (BOT_WORK_CALENDAR_ACCOUNT)")
        conference = bool(parsed.get("conference"))
        try:
            ev = await gcal_write.insert_event(
                pool, client_secrets_path=cfg.gcal_client_secrets,
                account_email=cfg.work_calendar_account,
                summary=parsed.get("title") or "(untitled)",
                description=parsed.get("description") or None,
                start=parsed.get("start"), end=parsed.get("end"),
                all_day=bool(parsed.get("all_day")),
                location=None if conference else (parsed.get("location") or None),
                tz=parsed.get("tz"), attendee_email=attendee_email,
                conference=conference,
            )
        except gcal_write.CalendarAuthError:
            # Leave the capture pending so a retry works after re-consent.
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                detail="calendar not authorized — re-run OAuth with calendar-write")
        except Exception as e:
            log.exception("calendar insert failed")
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"calendar error: {e}")
        # Keep the Google event id on the capture so the bot can keep editing
        # the real event after creation (✏️ Edit event → /update-event).
        parsed["gcal_event_id"] = ev.get("event_id")
        parsed["gcal_link"] = ev.get("html_link")
        await queries.update_capture_parsed(pool, capture_id, parsed, "event")
        await queries.mark_capture_decided(
            pool, capture_id, status="confirmed", result_kind="event",
            result_ref=ev.get("html_link") or ev.get("event_id"))
        line = f"✅ Added to calendar:\n{parsed.get('title')}"
        if ev.get("hangout_link"):
            line += f"\n🎥 Google Meet: {ev['hangout_link']}"
        elif parsed.get("location"):
            line += f"\n📍 {parsed['location']}"
        if attendee_email:
            line += f"\n📧 Invite sent to {attendee_email}"
        return {"ok": True, "result_kind": "event", "editable": True, "summary": line}

    @app.post("/api/capture")
    async def capture_route(body: CaptureIn, request: Request, _: None = Depends(auth.verify)):
        """Classify a text/voice note into a task/opp/event and park it as a
        pending bot_capture. Nothing is created until /confirm."""
        pool = request.app.state.pool
        client = request.app.state.anthropic
        if client is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                detail="capture unavailable — ANTHROPIC_API_KEY not configured")
        text = (body.text or "").strip()
        if not text and not body.image_b64:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="text or image required")
        owner = body.owner if body.owner in ("me", "wife", "son") else "me"
        finance_only = _is_finance_scope(owner)
        try:
            parsed = await _capture_parse(
                pool, client, request.app.state.cfg.extraction_model, text,
                image_b64=body.image_b64, image_media_type=body.image_media_type,
                finance_only=finance_only)
        except Exception as e:
            log.exception("capture classify failed")
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"classify error: {e}")
        if parsed is None:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="model returned no classification")
        # Finance-scoped members (wife/son) can only log spending — anything else
        # is refused WITHOUT creating a capture, so no CRM/calendar surface opens.
        if finance_only and parsed.get("type") not in ("expense", "income", "transfer"):
            return {"id": None, "type": parsed.get("type"), "blocked": "finance_only",
                    "summary": "🚫 This bot logs your expenses & income. Send a "
                               "spend/receipt (e.g. \"spent 200 baht groceries\" or a slip photo)."}
        # For a photo capture there's no caption — keep a readable raw_text so the
        # capture list and any later reclassify have something to show.
        raw = text or (parsed.get("note") or parsed.get("payee") or "📷 receipt photo")
        parsed["owner"] = owner                   # who logged it → transaction tag on confirm
        cap = await queries.create_bot_capture(
            pool, source=body.source, raw_text=raw, parsed=parsed, result_kind=parsed.get("type"),
        )
        return {"id": cap["id"], "type": parsed.get("type"), "summary": _render_capture_summary(parsed)}

    @app.post("/api/capture/{capture_id}/reclassify")
    async def capture_reclassify_route(
        capture_id: str, body: CaptureReclassifyIn, request: Request, _: None = Depends(auth.verify),
    ):
        pool = request.app.state.pool
        client = request.app.state.anthropic
        if client is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="extraction unavailable")
        cap = await queries.get_bot_capture(pool, capture_id)
        if cap is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="capture not found")
        if cap["status"] != "pending":
            raise HTTPException(status.HTTP_409_CONFLICT, detail=f"already {cap['status']}")
        cap_owner = (cap.get("parsed") or {}).get("owner") or "me"
        cap_finance_only = _is_finance_scope(cap_owner)
        if cap_finance_only and body.target_type not in ("expense", "income", "transfer"):
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                detail="this bot logs expenses & income only")
        parsed = await _capture_parse(
            pool, client, request.app.state.cfg.extraction_model, cap["raw_text"],
            force_type=body.target_type, finance_only=cap_finance_only,
        )
        if parsed is None:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="model returned no classification")
        parsed["owner"] = (cap.get("parsed") or {}).get("owner") or "me"   # survive change-type
        await queries.update_capture_parsed(pool, capture_id, parsed, parsed.get("type"))
        return {"id": capture_id, "type": parsed.get("type"), "summary": _render_capture_summary(parsed)}

    def _is_created_event(cap: dict) -> bool:
        """A confirmed event capture whose Google event we can still edit."""
        return (cap["status"] == "confirmed"
                and (cap["parsed"] or {}).get("type") == "event"
                and bool((cap["parsed"] or {}).get("gcal_event_id")))

    async def _get_pending_capture(pool, capture_id: str) -> dict:
        cap = await queries.get_bot_capture(pool, capture_id)
        if cap is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="capture not found")
        if cap["status"] != "pending":
            raise HTTPException(status.HTTP_409_CONFLICT, detail=f"already {cap['status']}")
        return cap

    async def _get_editable_capture(pool, capture_id: str) -> dict:
        """Pending capture, OR a confirmed event that has a Google event id
        (post-create editing from the bot)."""
        cap = await queries.get_bot_capture(pool, capture_id)
        if cap is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="capture not found")
        if cap["status"] != "pending" and not _is_created_event(cap):
            raise HTTPException(status.HTTP_409_CONFLICT, detail=f"already {cap['status']}")
        return cap

    @app.get("/api/capture/{capture_id}")
    async def capture_get_route(
        capture_id: str, request: Request, _: None = Depends(auth.verify),
    ):
        """Re-render a capture's card — used by the bot's Edit/Back flow.
        `conference` lets the bot label the Meet toggle for event captures;
        `created` flags a confirmed event (post-create edit mode)."""
        cap = await _get_editable_capture(request.app.state.pool, capture_id)
        parsed = cap["parsed"]
        return {"id": capture_id, "type": parsed.get("type"),
                "conference": bool(parsed.get("conference")),
                "all_day": bool(parsed.get("all_day")),
                "created": _is_created_event(cap),
                "repeat": parsed.get("repeat") or "none",
                "repeat_weekdays": parsed.get("repeat_weekdays") or [],
                "summary": _render_capture_summary(parsed)}

    @app.get("/api/capture/{capture_id}/people")
    async def capture_people_route(
        capture_id: str, request: Request, q: str = "", _: None = Depends(auth.verify),
    ):
        """Search contacts for the Invitee/Contact picker. Stashes the result
        list (and the typed query) into the capture's parsed blob so the pick
        callback can reference a choice by index (callback_data is 64-byte
        capped — can't carry a UUID per button)."""
        pool = request.app.state.pool
        cap = await _get_pending_capture(pool, capture_id)
        ql = (q or "").strip()
        people = await queries.list_persons(pool, q=ql, limit=6, offset=0) if ql else []
        choices = [{"id": p["person_id"], "name": p["display_name"]} for p in people]
        parsed = cap["parsed"]
        parsed["_person_choices"] = choices
        parsed["_person_typed"] = ql
        await queries.update_capture_parsed(pool, capture_id, parsed, parsed.get("type") or cap["result_kind"])
        return {"choices": choices, "typed": ql}

    @app.get("/api/capture/{capture_id}/accounts")
    async def capture_accounts_route(capture_id: str, request: Request, _: None = Depends(auth.verify)):
        """Budget accounts for the expense/income account picker; stashes the
        choice list so the bot can pick by index (callback_data cap)."""
        pool = request.app.state.pool
        cap = await _get_pending_capture(pool, capture_id)
        choices = await queries.fin_accounts_for_picker(pool)
        parsed = cap["parsed"]
        parsed["_account_choices"] = choices
        await queries.update_capture_parsed(pool, capture_id, parsed, parsed.get("type") or cap["result_kind"])
        return {"choices": choices}

    @app.get("/api/capture/{capture_id}/categories")
    async def capture_categories_route(
        capture_id: str, request: Request, q: str = "", _: None = Depends(auth.verify),
    ):
        """Search budget categories for the expense/income category picker."""
        pool = request.app.state.pool
        cap = await _get_pending_capture(pool, capture_id)
        parsed = cap["parsed"]
        ql = (q or "").strip()
        kind = parsed.get("type") or "expense"
        cats = await queries.fin_categories_search(pool, ql, kind) if ql else []
        parsed["_category_choices"] = cats
        await queries.update_capture_parsed(pool, capture_id, parsed, parsed.get("type") or cap["result_kind"])
        return {"choices": cats, "typed": ql}

    @app.patch("/api/capture/{capture_id}")
    async def capture_patch_route(
        capture_id: str, body: CapturePatchIn, request: Request, _: None = Depends(auth.verify),
    ):
        """Edit a capture's fields in place (bot Edit menu). No LLM call —
        mutate `parsed`, persist, return the re-rendered card. Also works on a
        confirmed event capture (post-create edits, saved via /update-event)."""
        pool = request.app.state.pool
        cap = await _get_editable_capture(pool, capture_id)
        parsed = cap["parsed"]

        if body.title is not None:
            parsed["title"] = body.title.strip()

        if body.clear_due_date:
            parsed["due_date"] = None
        elif body.due_date is not None:
            d = _parse_due_date(body.due_date)
            if d is None:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="couldn't read that as a date")
            parsed["due_date"] = d.isoformat()

        if body.clear_person:
            parsed["person_id"] = parsed["person_display"] = parsed["person_name"] = None
            parsed["person_matched"] = False
        elif body.person_choice_idx is not None:
            choices = parsed.get("_person_choices") or []
            if not (0 <= body.person_choice_idx < len(choices)):
                raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="person choice out of range")
            c = choices[body.person_choice_idx]
            parsed["person_id"] = c["id"]
            parsed["person_display"] = parsed["person_name"] = c["name"]
            parsed["person_matched"] = True
            _retitle_event_for_person(parsed, c["name"])
        elif body.use_typed_person:
            nm = (parsed.get("_person_typed") or "").strip()
            if not nm:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="no typed name to use")
            parsed["person_id"] = parsed["person_display"] = None
            parsed["person_matched"] = False
            parsed["person_name"] = nm
            _retitle_event_for_person(parsed, nm)

        if body.clear_project:
            parsed["project_id"] = parsed["project_name"] = parsed["project_slug"] = None
        elif body.project_idx is not None:
            projects = await queries.list_projects(pool, status=None)
            if not (0 <= body.project_idx < len(projects)):
                raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="project choice out of range")
            p = projects[body.project_idx]
            parsed["project_id"], parsed["project_name"], parsed["project_slug"] = p["id"], p["name"], p["slug"]

        # --- event-only edits (date / time / location / conference) ---
        event_touched = False
        if body.event_date is not None:
            d = _parse_due_date(body.event_date)
            if d is None:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="couldn't read that as a date")
            parsed["event_date"] = d.isoformat()
            event_touched = True
        if body.event_time is not None:
            raw = body.event_time.strip()
            start_s, _, end_s = raw.partition("-")
            st = _norm_time(start_s)
            if st is None:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="time must be HH:MM (or HH:MM-HH:MM)")
            en = _norm_time(end_s) if end_s.strip() else ""
            if end_s.strip() and en is None:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="end time must be HH:MM")
            parsed["event_time"], parsed["event_end_time"] = st, (en or "")
            parsed["all_day"] = False
            event_touched = True
        if body.toggle_all_day:
            parsed["all_day"] = not bool(parsed.get("all_day"))
            if parsed["all_day"]:
                # an all-day event has no clock times
                parsed["event_time"], parsed["event_end_time"] = "", ""
            event_touched = True
        if body.clear_location:
            parsed["location"] = None
        elif body.location is not None:
            parsed["location"] = body.location.strip()
            parsed["conference"] = False   # a place means it's in-person
        if body.toggle_conference:
            parsed["conference"] = not bool(parsed.get("conference"))
            if parsed["conference"]:
                parsed["location"] = None
        if event_touched:
            _assemble_event_times(parsed)

        # --- task scheduling edits (recurrence / time / duration) ---
        if body.clear_task_time:
            parsed["task_time"] = ""
        elif body.task_time is not None:
            t = _parse_clock(body.task_time)
            if t is None:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="time must be HH:MM")
            parsed["task_time"] = t.strftime("%H:%M")
        if body.repeat is not None:
            parsed["repeat"] = body.repeat
            if body.repeat != "weekly":
                parsed["repeat_weekdays"] = []
        if body.toggle_weekday is not None and 0 <= body.toggle_weekday <= 6:
            parsed["repeat"] = "weekly"
            days = set(parsed.get("repeat_weekdays") or [])
            days ^= {body.toggle_weekday}
            parsed["repeat_weekdays"] = sorted(days)
        if body.clear_duration:
            parsed["duration_min"] = None
        elif body.duration_min is not None:
            parsed["duration_min"] = body.duration_min if body.duration_min > 0 else None

        if body.clear_calendar:
            parsed["calendar_account"] = None
        elif body.calendar_account is not None:
            parsed["calendar_account"] = body.calendar_account.strip().lower() or None

        # --- expense / income edits ---
        if body.amount is not None:
            if body.amount <= 0:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="amount must be a positive number")
            parsed["amount"] = float(body.amount)
        if body.currency is not None:
            parsed["currency"] = body.currency.strip().upper() or "THB"
        if body.payee_text is not None:
            parsed["payee"] = body.payee_text.strip()
        if body.txn_date is not None:
            d = _parse_due_date(body.txn_date)
            if d is None:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="couldn't read that as a date")
            parsed["txn_date"] = d.isoformat()
        # --- transfer leg edits (source / destination accounts) ---
        # Explicit side, else the plain account picker fills whichever leg is
        # still empty (source first) so the existing single "Account" button
        # completes a transfer.
        def _pick_account(idx: int) -> dict:
            choices = parsed.get("_account_choices") or []
            if not (0 <= idx < len(choices)):
                raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="account choice out of range")
            return choices[idx]

        if body.from_account_choice_idx is not None:
            c = _pick_account(body.from_account_choice_idx)
            parsed["from_account_id"], parsed["from_account_name"] = c["id"], c["name"]
        if body.to_account_choice_idx is not None:
            c = _pick_account(body.to_account_choice_idx)
            parsed["to_account_id"], parsed["to_account_name"] = c["id"], c["name"]
        if body.account_choice_idx is not None:
            c = _pick_account(body.account_choice_idx)
            if parsed.get("type") == "transfer":
                side = "from" if not parsed.get("from_account_id") else "to"
                parsed[f"{side}_account_id"], parsed[f"{side}_account_name"] = c["id"], c["name"]
            else:
                parsed["account_id"], parsed["account_name"] = c["id"], c["name"]
        if body.clear_category:
            parsed["category_key"] = parsed["category_label"] = None
        elif body.category_choice_idx is not None:
            choices = parsed.get("_category_choices") or []
            if not (0 <= body.category_choice_idx < len(choices)):
                raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="category choice out of range")
            c = choices[body.category_choice_idx]
            parsed["category_key"], parsed["category_label"] = c["key"], c["label"]

        await queries.update_capture_parsed(pool, capture_id, parsed, parsed.get("type"))
        return {"id": capture_id, "type": parsed.get("type"),
                "all_day": bool(parsed.get("all_day")),
                "created": _is_created_event(cap),
                "repeat": parsed.get("repeat") or "none",
                "repeat_weekdays": parsed.get("repeat_weekdays") or [],
                "summary": _render_capture_summary(parsed)}

    @app.post("/api/capture/{capture_id}/confirm")
    async def capture_confirm_route(
        capture_id: str, body: CaptureConfirmIn, request: Request, _: None = Depends(auth.verify),
    ):
        pool = request.app.state.pool
        cfg = request.app.state.cfg
        cap = await queries.get_bot_capture(pool, capture_id)
        if cap is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="capture not found")
        if cap["status"] != "pending":
            raise HTTPException(status.HTTP_409_CONFLICT, detail=f"already {cap['status']}")
        parsed = cap["parsed"]
        if body.override_type and body.override_type != parsed.get("type"):
            client = request.app.state.anthropic
            if client is not None:
                re_parsed = await _capture_parse(
                    pool, client, cfg.extraction_model, cap["raw_text"], force_type=body.override_type)
                if re_parsed:
                    parsed = re_parsed
                    await queries.update_capture_parsed(pool, capture_id, parsed, parsed.get("type"))
        ctype = parsed.get("type")

        if ctype == "opportunity":
            opp = await queries.create_opportunity(pool, {
                "title": parsed.get("title"), "description": parsed.get("description") or None,
                "project_id": parsed.get("project_id"), "counterparty_id": parsed.get("person_id"),
                "stage": parsed.get("stage") or "intro", "estimated_value": parsed.get("value") or None,
                "source_kind": "telegram_capture", "source_ref": capture_id,
            })
            await queries.mark_capture_decided(
                pool, capture_id, status="confirmed", result_kind="opportunity", result_id=opp["id"])
            return {"ok": True, "result_kind": "opportunity", "result_id": opp["id"],
                    "summary": f"✅ Created opportunity:\n{opp['title']}"}

        if ctype == "event":
            # If the matched contact has emails on file, ask whether to invite
            # one before creating (the bot shows the addresses as buttons).
            person_id = parsed.get("person_id")
            emails = await queries.person_emails(pool, person_id) if person_id else []
            if emails:
                parsed["invite_emails"] = emails[:6]
                await queries.update_capture_parsed(pool, capture_id, parsed, "event")
                who = parsed.get("person_display") or parsed.get("person_name") or "them"
                return {"ok": True, "needs": "invite_choice", "emails": emails[:6],
                        "summary": f"Should I send the invitation to {who}?"}
            return await _finalize_event(pool, cfg, capture_id, parsed, None)

        if ctype in ("expense", "income"):
            amount = parsed.get("amount") or 0
            if not amount or amount <= 0:
                raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                    detail="add an amount before confirming")
            account_id = parsed.get("account_id")
            if not account_id:
                raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                    detail="pick an account before confirming")
            asset_id = await queries.resolve_asset_id(pool, parsed.get("currency") or "THB")
            is_expense = ctype == "expense"
            txn = await queries.create_fin_transaction(pool, {
                "txn_date": _parse_due_date(parsed.get("txn_date")) or _bangkok_today(),
                "outflow_account_id": account_id if is_expense else None,
                "outflow_asset_id": asset_id if is_expense else None,
                "outflow_amount": amount if is_expense else None,
                "inflow_account_id": None if is_expense else account_id,
                "inflow_asset_id": None if is_expense else asset_id,
                "inflow_amount": None if is_expense else amount,
                "category_key": parsed.get("category_key"),
                "payee_text": parsed.get("payee") or None,
                "note": parsed.get("description") or parsed.get("title") or None,
                # tag who logged it (wife/son) so the household can filter; 'me' stays untagged
                "tags": [parsed["owner"]] if parsed.get("owner") in ("wife", "son") else [],
                "source_kind": "telegram_capture", "source_ref": capture_id,
            })
            await queries.mark_capture_decided(
                pool, capture_id, status="confirmed", result_kind=ctype, result_id=txn["id"])
            head = "💸 Expense" if is_expense else "💰 Income"
            line = (f"✅ {head} recorded:\n{amount:g} {parsed.get('currency') or 'THB'}"
                    f"{' · ' + parsed['category_label'] if parsed.get('category_label') else ''}"
                    f"\n{'from' if is_expense else 'to'} {parsed.get('account_name')}")
            return {"ok": True, "result_kind": ctype, "result_id": txn["id"], "summary": line}

        if ctype == "transfer":
            amount = parsed.get("amount") or 0
            if not amount or amount <= 0:
                raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                    detail="add an amount before confirming")
            from_id, to_id = parsed.get("from_account_id"), parsed.get("to_account_id")
            if not from_id or not to_id:
                missing = "source" if not from_id else "destination"
                raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                    detail=f"set the {missing} account before confirming")
            if from_id == to_id:
                raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                    detail="source and destination must be different accounts")
            asset_id = await queries.resolve_asset_id(pool, parsed.get("currency") or "THB")
            txn = await queries.create_fin_transaction(pool, {
                "txn_date": _parse_due_date(parsed.get("txn_date")) or _bangkok_today(),
                "outflow_account_id": from_id, "outflow_asset_id": asset_id, "outflow_amount": amount,
                "inflow_account_id": to_id, "inflow_asset_id": asset_id, "inflow_amount": amount,
                "category_key": None, "payee_text": None,
                "note": parsed.get("description") or parsed.get("title") or None,
                "tags": [parsed["owner"]] if parsed.get("owner") in ("wife", "son") else [],
                "source_kind": "telegram_capture", "source_ref": capture_id,
            })
            await queries.mark_capture_decided(
                pool, capture_id, status="confirmed", result_kind=ctype, result_id=txn["id"])
            line = (f"✅ 🔀 Transfer recorded:\n{amount:g} {parsed.get('currency') or 'THB'}"
                    f"\n{parsed.get('from_account_name')} → {parsed.get('to_account_name')}")
            return {"ok": True, "result_kind": ctype, "result_id": txn["id"], "summary": line}

        if ctype == "tasks":
            items = [s.strip() for s in (parsed.get("items") or []) if isinstance(s, str) and s.strip()]
            if not items:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="no items to add")
            project_id = parsed.get("project_id")
            project_name = parsed.get("project_name")
            if not project_id:   # default bucket when the note named no project
                project_id = await queries.get_or_create_project_by_name(pool, "Shopping List")
                project_name = "Shopping List"
            for it in items:
                await queries.create_task(pool, {
                    "title": it, "project_id": project_id, "status": "open",
                    "source_kind": "telegram_capture", "source_ref": capture_id,
                })
            await queries.mark_capture_decided(
                pool, capture_id, status="confirmed", result_kind="tasks", result_ref=capture_id)
            listing = "\n".join(f"• {c}" for c in items[:20])
            more = f"\n…and {len(items) - 20} more" if len(items) > 20 else ""
            return {"ok": True, "result_kind": "tasks",
                    "summary": f"✅ Added {len(items)} tasks to {project_name}:\n{listing}{more}"}

        # default → task, OR a recurring routine when repeat is set.
        rep = parsed.get("repeat") or "none"
        at_time = _parse_clock(parsed.get("task_time"))
        duration = parsed.get("duration_min")
        if rep != "none":
            freq = "weekly" if rep in ("weekdays", "weekly") else rep
            byweekday = [0, 1, 2, 3, 4] if rep == "weekdays" else (parsed.get("repeat_weekdays") or [])
            routine = await queries.create_recurring_task(pool, {
                "title": parsed.get("title"), "description": parsed.get("description") or None,
                "project_id": parsed.get("project_id"), "with_person_id": parsed.get("person_id"),
                "at_time": at_time, "duration_min": duration,
                "freq": freq, "byweekday": byweekday, "anchor_date": _bangkok_today(),
            })
            await queries.generate_routines_for(pool, _bangkok_today())
            await queries.mark_capture_decided(
                pool, capture_id, status="confirmed", result_kind="routine", result_ref=routine["id"])
            human = _humanize_repeat(rep, byweekday, parsed.get("task_time"))
            line = f"✅ Created routine:\n{routine['title']}\n🔁 {human}"
            acct = parsed.get("calendar_account")
            if acct:
                try:
                    await _sync_routine_calendar(pool, cfg, routine, acct)
                    line += f"\n📅 On {acct}'s calendar"
                except gcal_write.CalendarAuthError:
                    line += f"\n⚠️ {acct} not authorized for calendar"
                except Exception:
                    log.exception("routine calendar sync failed on confirm")
            return {"ok": True, "result_kind": "routine", "summary": line}

        task = await queries.create_task(pool, {
            "title": parsed.get("title"), "description": parsed.get("description") or None,
            "project_id": parsed.get("project_id"), "with_person_id": parsed.get("person_id"),
            "status": "open", "due_date": _parse_due_date(parsed.get("due_date")),
            "due_time": at_time, "duration_min": duration,
            "source_kind": "telegram_capture", "source_ref": capture_id,
        })
        await queries.mark_capture_decided(
            pool, capture_id, status="confirmed", result_kind="task", result_id=task["id"])
        line = f"✅ Created task:\n{task['title']}"
        acct = parsed.get("calendar_account")
        if acct and task.get("due_date"):
            try:
                await _sync_task_calendar(pool, cfg, task, acct)
                line += f"\n📅 On {acct}'s calendar"
            except gcal_write.CalendarAuthError:
                line += f"\n⚠️ {acct} not authorized for calendar"
            except Exception:
                log.exception("task calendar sync failed on confirm")
        elif acct:
            line += "\n(give it a date to add it to a calendar)"
        return {"ok": True, "result_kind": "task", "result_id": task["id"],
                "summary": line}

    @app.post("/api/capture/{capture_id}/discard")
    async def capture_discard_route(
        capture_id: str, request: Request, _: None = Depends(auth.verify),
    ):
        pool = request.app.state.pool
        cap = await queries.get_bot_capture(pool, capture_id)
        if cap is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="capture not found")
        if cap["status"] != "pending":
            raise HTTPException(status.HTTP_409_CONFLICT, detail=f"already {cap['status']}")
        await queries.mark_capture_decided(pool, capture_id, status="discarded")
        return {"ok": True}

    @app.post("/api/capture/{capture_id}/invite")
    async def capture_invite_route(
        capture_id: str, body: CaptureInviteIn, request: Request, _: None = Depends(auth.verify),
    ):
        """Create the (still-pending) event, optionally inviting the email at
        `index` of the capture's stored invite_emails. index None/out-of-range
        → create without an attendee ('Don't invite')."""
        pool = request.app.state.pool
        cfg = request.app.state.cfg
        cap = await queries.get_bot_capture(pool, capture_id)
        if cap is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="capture not found")
        if cap["status"] != "pending":
            raise HTTPException(status.HTTP_409_CONFLICT, detail=f"already {cap['status']}")
        parsed = cap["parsed"]
        emails = parsed.get("invite_emails") or []
        attendee = emails[body.index] if (body.index is not None and 0 <= body.index < len(emails)) else None
        return await _finalize_event(pool, cfg, capture_id, parsed, attendee)

    @app.post("/api/capture/{capture_id}/update-event")
    async def capture_update_event_route(
        capture_id: str, request: Request, _: None = Depends(auth.verify),
    ):
        """Push a confirmed event capture's (bot-edited) fields to the real
        Google Calendar event. Patch semantics — attendees and a Meet link
        survive; the invitee is emailed the change."""
        pool = request.app.state.pool
        cfg = request.app.state.cfg
        cap = await queries.get_bot_capture(pool, capture_id)
        if cap is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="capture not found")
        if not _is_created_event(cap):
            raise HTTPException(status.HTTP_409_CONFLICT, detail="no created event to update")
        parsed = cap["parsed"]
        try:
            ev = await gcal_write.update_event(
                pool, client_secrets_path=cfg.gcal_client_secrets,
                account_email=cfg.work_calendar_account,
                event_id=parsed["gcal_event_id"],
                summary=parsed.get("title") or "(untitled)",
                description=parsed.get("description") or None,
                start=parsed.get("start"), end=parsed.get("end"),
                all_day=bool(parsed.get("all_day")),
                location=None if parsed.get("conference") else (parsed.get("location") or None),
                tz=parsed.get("tz"),
            )
        except gcal_write.CalendarAuthError:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                detail="calendar not authorized — re-run OAuth with calendar-write")
        except Exception as e:
            log.exception("calendar update failed")
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"calendar error: {e}")
        line = f"✅ Calendar event updated:\n{parsed.get('title')}"
        if ev.get("hangout_link"):
            line += f"\n🎥 Google Meet: {ev['hangout_link']}"
        elif parsed.get("location"):
            line += f"\n📍 {parsed['location']}"
        when = (parsed.get("start") or "")[:10] if parsed.get("all_day") else _fmt_capture_dt(parsed.get("start"))
        if when:
            line += f"\n🕐 {when}" + (" (all day)" if parsed.get("all_day") else "")
        return {"ok": True, "result_kind": "event", "editable": True, "summary": line}

    @app.get("/api/suggestions", response_model=list[SuggestionRow])
    async def list_suggestions_route(
        request: Request,
        status_filter: str | None = None,
        kind: str | None = None,
        limit: int = 100, offset: int = 0,
        _: None = Depends(auth.verify),
    ):
        # default to pending-only when no explicit filter
        st = status_filter if status_filter is not None else "pending"
        if st == "all":
            st = None
        limit = max(1, min(limit, 200))
        return await queries.list_suggestions(
            request.app.state.pool, status=st, kind=kind, limit=limit, offset=offset,
        )

    @app.get("/api/suggestions/count")
    async def count_suggestions_route(request: Request, _: None = Depends(auth.verify)):
        return {"pending": await queries.count_pending_suggestions(request.app.state.pool)}

    @app.post("/api/suggestions/{suggestion_id}/accept")
    async def accept_suggestion_route(
        suggestion_id: str, request: Request, _: None = Depends(auth.verify),
    ):
        out = await queries.accept_suggestion(request.app.state.pool, suggestion_id)
        if out is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="suggestion not pending / not found")
        return {"ok": True, **out}

    @app.post("/api/suggestions/{suggestion_id}/dismiss", status_code=204)
    async def dismiss_suggestion_route(
        suggestion_id: str, request: Request, _: None = Depends(auth.verify),
    ):
        ok = await queries.dismiss_suggestion(request.app.state.pool, suggestion_id)
        if not ok:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="suggestion not pending / not found")
        return None

    @app.post("/api/suggestions/{suggestion_id}/reassign", response_model=SuggestionRow)
    async def reassign_suggestion_route(
        suggestion_id: str, body: SuggestionReassignIn, request: Request,
        _: None = Depends(auth.verify),
    ):
        """Re-point a pending suggestion at a different person (fixes a
        mis-resolved name before accepting)."""
        row = await queries.reassign_suggestion_person(
            request.app.state.pool, suggestion_id, body.person_id,
        )
        if row is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail="suggestion not pending, or person not found",
            )
        return row

    # --- draft outreach (Phase 5b, DRAFT ONLY — never auto-sent) --------

    @app.get("/api/persons/{person_id}/drafts", response_model=list[DraftRow])
    async def list_drafts_route(
        person_id: str, request: Request, _: None = Depends(auth.verify),
    ):
        return await queries.list_drafts_for_person(request.app.state.pool, person_id)

    @app.post("/api/persons/{person_id}/drafts/generate", response_model=DraftRow)
    async def generate_draft_route(
        person_id: str, body: DraftGenerateIn, request: Request,
        _: None = Depends(auth.verify),
    ):
        """Draft a follow-up grounded in the recent thread + an open item.
        Stores it as a 'draft' for review — does NOT send anything. A contact
        marked `sensitive` routes to the LOCAL Ollama (their messages never
        reach the cloud; slower, model provenance stored on the draft)."""
        pool = request.app.state.pool
        client = request.app.state.anthropic
        async with pool.acquire() as conn:
            prow = await conn.fetchrow(
                "SELECT display_name, sensitive FROM canonical.person WHERE id=$1::uuid AND merged_into IS NULL",
                person_id,
            )
        if prow is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="person not found")
        person_name = prow["display_name"]
        sensitive = bool(prow["sensitive"])
        # The Anthropic key is only required for the cloud branch — sensitive
        # drafts must work with no cloud configured at all.
        if not sensitive and client is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="draft unavailable — ANTHROPIC_API_KEY not configured",
            )

        # Context label: explicit task/opp if given, else the person's top
        # open task or live opp.
        context_label = None
        if body.task_id:
            t = await queries.get_task(pool, body.task_id)
            if t:
                context_label = f"Task: {t['title']}" + (f" — {t['description']}" if t.get("description") else "")
        elif body.opportunity_id:
            o = await queries.get_opportunity(pool, body.opportunity_id)
            if o:
                context_label = f"Opportunity ({o['stage']}): {o['title']}"
        if context_label is None:
            tasks = await queries.list_tasks_for_person(pool, person_id)
            open_tasks = [t for t in tasks if t["status"] in ("open", "doing")]
            if open_tasks:
                context_label = f"Task: {open_tasks[0]['title']}"
            else:
                opps = await queries.list_opps_for_person(pool, person_id)
                live = [o for o in opps if o["stage"] != "lost" and not o.get("closed_at")]
                if live:
                    context_label = f"Opportunity ({live[0]['stage']}): {live[0]['title']}"

        recent = await queries.recent_messages_for_draft(pool, person_id, limit=12)
        cfg = request.app.state.cfg
        try:
            if sensitive:
                log.info("draft route=local person=%s reason=sensitive", person_id)
                drafted = await extraction.draft_outreach_local(
                    ollama_url=cfg.ollama_url, ollama_model=cfg.ollama_model,
                    person_name=person_name, channel=body.channel,
                    self_label=cfg.self_label, recent_messages=recent,
                    context_label=context_label,
                )
            else:
                drafted = await extraction.draft_outreach(
                    client, cfg.extraction_model,
                    person_name=person_name, channel=body.channel,
                    self_label=cfg.self_label, recent_messages=recent,
                    context_label=context_label,
                )
        except Exception as e:
            # fail-closed for sensitive contacts: surface the error, never
            # retry via the cloud path
            log.exception("draft generation failed for person %s", person_id)
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"draft error: {e}")
        if not drafted.get("body"):
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="model returned an empty draft")

        return await queries.create_draft(pool, {
            "person_id": person_id, "channel": body.channel,
            "subject": drafted.get("subject"), "body": drafted["body"],
            "task_id": body.task_id, "opportunity_id": body.opportunity_id,
            "model": cfg.ollama_model if sensitive else cfg.extraction_model,
        })

    @app.patch("/api/drafts/{draft_id}", response_model=DraftRow)
    async def patch_draft_route(
        draft_id: str, body: DraftPatch, request: Request,
        _: None = Depends(auth.verify),
    ):
        """Edit a draft, mark it sent (manual — you sent it elsewhere), or
        discard it. No channel send happens here."""
        fields = body.model_dump(exclude_unset=True)
        row = await queries.patch_draft(request.app.state.pool, draft_id, fields)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="draft not found")
        return row

    @app.post("/api/drafts/{draft_id}/send")
    async def send_draft_route(
        draft_id: str, request: Request, _: None = Depends(auth.verify),
    ):
        """Explicit user action: send a reviewed draft for real, FROM the user's
        account. Telegram → enqueue to memory.telegram_outbox (the live Telethon
        process sends it). Email → send directly via the Gmail API. Nothing is
        sent without this call."""
        pool = request.app.state.pool
        cfg = request.app.state.cfg
        draft = await queries.get_draft(pool, draft_id)
        if draft is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="draft not found")
        if draft["status"] != "draft":
            raise HTTPException(status.HTTP_409_CONFLICT, detail=f"draft already {draft['status']}")

        if draft["channel"] == "email":
            if not cfg.email_send_account:
                raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                    detail="email sending not configured (BOT_EMAIL_SEND_ACCOUNT)")
            emails = await queries.person_emails(pool, draft["person_id"])
            if not emails:
                raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                    detail="this contact has no email to send to")
            to_email = emails[0]
            try:
                res = await gmail_send.send_email(
                    pool, client_secrets_path=cfg.gcal_client_secrets,
                    account_email=cfg.email_send_account, to_email=to_email,
                    subject=draft.get("subject"), body=draft["body"],
                )
            except gmail_send.GmailAuthError:
                raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                    detail="email not authorized — re-consent the send account with gmail.send")
            except Exception as e:
                log.exception("email send failed")
                raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"email error: {e}")
            await queries.patch_draft(pool, draft_id, {"status": "sent"})
            return {"ok": True, "sent": True, "to": to_email, "message_id": res.get("message_id")}

        # telegram (default)
        tg_id = await queries.person_telegram_id(pool, draft["person_id"])
        if tg_id is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                detail="this contact has no Telegram identity to send to")
        row = await queries.enqueue_telegram_send(
            pool, person_id=draft["person_id"], draft_id=draft_id,
            tg_user_id=tg_id, body=draft["body"],
        )
        return {"ok": True, "queued": True, "outbox_id": row["id"]}

    # --- daily plan (Phase 4) ------------------------------------------

    @app.get("/api/today", response_model=DailyPlanRow | None)
    async def today_route(request: Request, _: None = Depends(auth.verify)):
        """The latest daily plan (by the day it's for). Null when none has
        been generated yet — the UI prompts a first Regenerate."""
        return await queries.get_latest_daily_plan(request.app.state.pool)

    @app.get("/api/today/counts")
    async def today_counts_route(request: Request, _: None = Depends(auth.verify)):
        """Live 'state of the board' counts for the /today pills, computed now
        (the plan snapshot's counts go stale between regenerations). Meetings
        are scoped to today (Asia/Bangkok)."""
        from datetime import datetime, time as _time, timedelta
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Asia/Bangkok")
        today = datetime.now(tz).date()
        day_start = datetime.combine(today, _time.min, tzinfo=tz)
        day_end = day_start + timedelta(days=1)
        return await queries.today_counts(
            request.app.state.pool, day_start=day_start, day_end=day_end,
        )

    @app.get("/api/stats")
    async def stats_route(request: Request, _: None = Depends(auth.verify)):
        """Gamified scoreboard: tasks done today/this week, current streak, a
        7-day (Mon–Sun) completion series, overdue/due-today, and live pipeline
        USD by stage. Day boundaries are Asia/Bangkok."""
        tz = ZoneInfo("Asia/Bangkok")
        today = datetime.now(tz).date()
        since = today - timedelta(days=60)
        monday = today - timedelta(days=today.weekday())
        raw = await queries.task_stats(
            request.app.state.pool, since_date=since, today=today, week_start=monday)
        comp = {d: n for d, n in raw["completions"]}
        done_today = comp.get(today, 0)
        dow = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        week = [{"date": (monday + timedelta(days=i)).isoformat(), "dow": dow[i],
                 "count": comp.get(monday + timedelta(days=i), 0),
                 "is_today": (monday + timedelta(days=i)) == today} for i in range(7)]
        done_week = sum(w["count"] for w in week)
        # Current streak: consecutive days with ≥1 completion. The day isn't over,
        # so a zero-today doesn't break a streak that's alive through yesterday.
        streak, d = 0, today
        if comp.get(today, 0) == 0:
            d = today - timedelta(days=1)
        while comp.get(d, 0) > 0:
            streak += 1
            d -= timedelta(days=1)
        # All-time best streak from the full history of completion days. The
        # current (possibly still-growing) streak counts toward the record.
        best, run, prev = 0, 0, None
        for d in raw["all_days"]:
            run = run + 1 if prev is not None and (d - prev).days == 1 else 1
            best = max(best, run)
            prev = d
        best_streak = max(best, streak)
        weekly_goal = int(await queries.get_setting(
            request.app.state.pool, "weekly_goal", 15) or 15)
        pipeline_total = sum(usd for _, _, usd in raw["pipeline"])
        by_stage = [{"stage": s, "count": n, "usd": usd} for s, n, usd in raw["pipeline"]]
        deals = sum(n for _, n, _ in raw["pipeline"])
        return {
            "done_today": done_today, "done_week": done_week, "week": week,
            "streak": streak, "best_streak": best_streak,
            "weekly_goal": weekly_goal,
            "projects_week": [{"name": nm, "count": n} for nm, n in raw["projects_week"]],
            "overdue": raw["overdue"], "due_today": raw["due_today"],
            "pipeline": {"total_usd": pipeline_total, "deals": deals, "by_stage": by_stage},
        }

    @app.put("/api/settings/weekly-goal")
    async def set_weekly_goal_route(
        body: WeeklyGoalIn, request: Request, _: None = Depends(auth.verify),
    ):
        """Set the /today weekly completion goal (drives the goal ring)."""
        await queries.set_setting(request.app.state.pool, "weekly_goal", body.goal)
        return {"ok": True, "weekly_goal": body.goal}

    @app.get("/api/events")
    async def list_events_route(
        request: Request, days: int = 2,
        start: str | None = None, end: str | None = None,
        _: None = Depends(auth.verify),
    ):
        """Live calendar agenda from raw.gcal_event — always as fresh as the last
        30-min gcal sync. Default: today + the next `days-1` days (Asia/Bangkok
        day boundaries). The Day/Week schedule views pass an explicit
        `start`/`end` (ISO date or datetime, Asia/Bangkok) to fetch any range."""
        tz = ZoneInfo("Asia/Bangkok")

        def _parse(s: str) -> datetime:
            d = datetime.fromisoformat(s)
            return d.replace(tzinfo=tz) if d.tzinfo is None else d

        if start:
            win_start = _parse(start)
            win_end = _parse(end) if end else win_start + timedelta(days=1)
            # cap the span so a bad param can't scan the whole table
            if win_end <= win_start or (win_end - win_start) > timedelta(days=31):
                win_end = win_start + timedelta(days=1)
        else:
            win_start = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
            win_end = win_start + timedelta(days=max(1, min(days, 14)))
        events = await queries.upcoming_events(request.app.state.pool, win_start, win_end)
        return {"events": events, "timezone": "Asia/Bangkok"}

    @app.post("/api/today/regenerate")
    async def regenerate_today_route(
        request: Request, _: None = Depends(auth.verify),
    ):
        """Run the daily_planner worker server-side for TODAY so the user can
        refresh the plan from the UI mid-day. Same subprocess pattern as
        /api/merge/regenerate. Inherits the API's env (DB + Anthropic key)
        and pins PLANNER_TARGET=today."""
        cmd = [
            "/srv/memory/apps/daily_planner/.venv/bin/python",
            "-m", "daily_planner",
        ]
        env = {**os.environ, "PLANNER_TARGET": "today"}
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd="/srv/memory/apps/daily_planner",
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        except asyncio.TimeoutError:
            proc.kill()
            raise HTTPException(
                status.HTTP_504_GATEWAY_TIMEOUT,
                detail="daily_planner timed out after 2 min",
            )
        if proc.returncode != 0:
            tail = (stderr or b"").decode("utf-8", errors="replace")[-500:]
            log.error("daily_planner failed (rc=%d): %s", proc.returncode, tail)
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"daily_planner exit {proc.returncode}: {tail}",
            )
        plan = await queries.get_latest_daily_plan(request.app.state.pool)
        return {
            "ok": True,
            "plan": plan,
            "log_tail": (stderr or b"").decode("utf-8", errors="replace")[-500:],
        }

    # --- projects / tasks / opportunities (Phase 1) --------------------

    @app.get("/api/projects", response_model=list[ProjectRow])
    async def list_projects_route(
        request: Request, status: str | None = None,
        role: str = Depends(auth.verify),
    ):
        pool = request.app.state.pool
        if role == "budget":   # only the projects the member is in
            return await queries.list_projects_for_member(
                pool, request.app.state.cfg.budget_person_id, status=status)
        return await queries.list_projects(pool, status=status)

    @app.post("/api/projects", response_model=ProjectRow)
    async def create_project_route(
        body: ProjectCreate, request: Request, _: None = Depends(auth.verify),
    ):
        try:
            row = await queries.create_project(
                request.app.state.pool,
                slug=body.slug, name=body.name, description=body.description,
                status=body.status, color=body.color,
            )
        except Exception as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))
        # Refresh via list to get the aggregate counts on the returned row
        rows = await queries.list_projects(request.app.state.pool, status=None)
        for r in rows:
            if r["id"] == row["id"]:
                return r
        return row

    @app.get("/api/projects/{slug_or_id}", response_model=ProjectDetail)
    async def get_project_route(
        slug_or_id: str, request: Request, role: str = Depends(auth.verify),
    ):
        pool = request.app.state.pool
        if role == "budget":
            await _budget_member_or_403(pool, request.app.state.cfg, slug_or_id)
        row = await queries.get_project(pool, slug_or_id)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="project not found")
        return row

    @app.get("/api/projects/{project_id}/recaps")
    async def project_recaps_route(
        project_id: str, request: Request, _: None = Depends(auth.verify),
    ):
        """Meeting recaps attributed to this project (Phase 2 context)."""
        return await queries.list_recaps_for_project(request.app.state.pool, project_id)

    @app.patch("/api/projects/{project_id}", response_model=ProjectDetail)
    async def patch_project_route(
        project_id: str, body: ProjectPatch, request: Request,
        _: None = Depends(auth.verify),
    ):
        fields = body.model_dump(exclude_unset=True)
        row = await queries.patch_project(request.app.state.pool, project_id, fields)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="project not found")
        return row

    @app.delete("/api/projects/{project_id}", status_code=204)
    async def delete_project_route(
        project_id: str, request: Request, _: None = Depends(auth.verify),
    ):
        ok = await queries.soft_delete_project(request.app.state.pool, project_id)
        if not ok:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="project not found")
        return None

    @app.post("/api/projects/{project_id}/members", status_code=204)
    async def add_project_member_route(
        project_id: str, body: ProjectMemberIn, request: Request,
        _: None = Depends(auth.verify),
    ):
        ok = await queries.add_project_member(
            request.app.state.pool, project_id, body.person_id, body.role,
        )
        if not ok:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="could not add member")
        return None

    @app.delete("/api/projects/{project_id}/members/{person_id}", status_code=204)
    async def remove_project_member_route(
        project_id: str, person_id: str, request: Request,
        _: None = Depends(auth.verify),
    ):
        ok = await queries.remove_project_member(
            request.app.state.pool, project_id, person_id,
        )
        if not ok:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="member not on project")
        return None

    # --- tasks --------------------------------------------------------

    @app.get("/api/tasks", response_model=list[TaskRow])
    async def list_tasks_route(
        request: Request,
        project_id: str | None = None,
        status: str | None = None,
        with_person_id: str | None = None,
        q: str | None = None,
        limit: int = 50, offset: int = 0,
        role: str = Depends(auth.verify),
    ):
        pool = request.app.state.pool
        if role == "budget":
            # budget caller may only list tasks WITHIN one of her projects
            await _budget_member_or_403(pool, request.app.state.cfg, project_id)
            with_person_id = None   # ignore cross-project filters
        limit = max(1, min(limit, 200))
        return await queries.list_tasks(
            pool,
            project_id=project_id, status=status,
            with_person_id=with_person_id, q=q,
            limit=limit, offset=offset,
        )

    @app.post("/api/tasks", response_model=TaskRow)
    async def create_task_route(
        body: TaskCreate, request: Request, role: str = Depends(auth.verify),
    ):
        pool = request.app.state.pool
        if role == "budget":   # new task must land in one of her projects
            await _budget_member_or_403(pool, request.app.state.cfg, body.project_id)
        return await queries.create_task(pool, body.model_dump(exclude_unset=False))

    @app.patch("/api/tasks/{task_id}", response_model=TaskRow)
    async def patch_task_route(
        task_id: str, body: TaskPatch, request: Request,
        role: str = Depends(auth.verify),
    ):
        pool = request.app.state.pool
        fields = body.model_dump(exclude_unset=True)
        if role == "budget":
            existing = await queries.get_task(pool, task_id)
            if existing is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, detail="task not found")
            await _budget_member_or_403(pool, request.app.state.cfg, existing.get("project_id"))
            if "project_id" in fields:   # can't move a task out to a non-member project
                await _budget_member_or_403(pool, request.app.state.cfg, fields["project_id"])
        row = await queries.patch_task(pool, task_id, fields)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="task not found")
        # Keep a synced calendar event in step: completed/cancelled or date-removed
        # → drop the event; otherwise refresh it from the new title/date/time.
        if row.get("gcal_event_id") and row.get("gcal_account"):
            cfg = request.app.state.cfg
            try:
                if row.get("status") in ("done", "cancelled") or not row.get("due_date"):
                    await _unsync_task_calendar(pool, cfg, row)
                elif fields.keys() & {"title", "due_date", "due_time", "description", "status"}:
                    await _sync_task_calendar(pool, cfg, row, row["gcal_account"],
                                              row.get("gcal_calendar_id"))
                row = await queries.get_task(pool, task_id) or row
            except gcal_write.CalendarAuthError:
                log.warning("calendar re-sync skipped for task %s — not authorized", task_id)
            except Exception:
                log.exception("calendar re-sync failed for task %s", task_id)
        return row

    @app.delete("/api/tasks/{task_id}", status_code=204)
    async def delete_task_route(
        task_id: str, request: Request, role: str = Depends(auth.verify),
    ):
        pool = request.app.state.pool
        task = await queries.get_task(pool, task_id)
        if role == "budget":
            if task is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, detail="task not found")
            await _budget_member_or_403(pool, request.app.state.cfg, task.get("project_id"))
        ok = await queries.soft_delete_task(pool, task_id)
        if not ok:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="task not found")
        if task and task.get("gcal_event_id") and task.get("gcal_account"):
            try:
                await _unsync_task_calendar(pool, request.app.state.cfg, task)
            except Exception:
                log.warning("could not remove calendar event for deleted task %s", task_id)
        return None

    @app.get("/api/tasks/{task_id}", response_model=TaskDetail)
    async def get_task_route(
        task_id: str, request: Request, role: str = Depends(auth.verify),
    ):
        pool = request.app.state.pool
        row = await queries.get_task_detail(pool, task_id)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="task not found")
        if role == "budget":
            await _budget_member_or_403(pool, request.app.state.cfg, row.get("project_id"))
        return row

    @app.post("/api/tasks/{task_id}/people", status_code=204)
    async def add_task_person_route(
        task_id: str, body: TaskPersonIn, request: Request,
        _: None = Depends(auth.verify),
    ):
        ok = await queries.add_task_person(request.app.state.pool, task_id, body.person_id)
        if not ok:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="could not add person")
        return None

    @app.delete("/api/tasks/{task_id}/people/{person_id}", status_code=204)
    async def remove_task_person_route(
        task_id: str, person_id: str, request: Request,
        _: None = Depends(auth.verify),
    ):
        ok = await queries.remove_task_person(request.app.state.pool, task_id, person_id)
        if not ok:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="person not on task")
        return None

    @app.post("/api/tasks/{task_id}/decompose")
    async def decompose_task_route(
        task_id: str, request: Request, _: None = Depends(auth.verify),
    ):
        """Propose subtasks via Haiku — does NOT create them (the UI shows
        them for confirmation). Returns {subtasks: [titles]}."""
        client = request.app.state.anthropic
        if client is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="decompose unavailable — ANTHROPIC_API_KEY not configured",
            )
        task = await queries.get_task(request.app.state.pool, task_id)
        if task is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="task not found")
        cfg = request.app.state.cfg
        try:
            subs = await extraction.decompose_task(
                client, cfg.extraction_model,
                title=task["title"], description=task.get("description"),
                project_name=task.get("project_name"),
            )
        except Exception as e:
            log.exception("decompose failed for task %s", task_id)
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"decompose error: {e}")
        return {"subtasks": subs}

    @app.post("/api/tasks/{task_id}/subtasks", response_model=TaskDetail)
    async def create_subtasks_route(
        task_id: str, body: SubtasksCreateIn, request: Request,
        _: None = Depends(auth.verify),
    ):
        """Create the confirmed subtasks under a parent. Subtasks inherit
        the parent's project. Returns the refreshed parent detail."""
        pool = request.app.state.pool
        parent = await queries.get_task(pool, task_id)
        if parent is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="task not found")
        for title in body.titles:
            title = (title or "").strip()
            if not title:
                continue
            await queries.create_task(pool, {
                "title": title,
                "project_id": parent.get("project_id"),
                "parent_task_id": task_id,
                "status": "open",
                "source_kind": "decompose",
            })
        return await queries.get_task_detail(pool, task_id)

    # --- recurring routines ------------------------------------------

    def _bangkok_today() -> date:
        return datetime.now(ZoneInfo("Asia/Bangkok")).date()

    @app.get("/api/routines", response_model=list[RecurringTaskRow])
    async def list_routines_route(request: Request, project_id: str | None = None,
                                  _: None = Depends(auth.verify)):
        return await queries.list_recurring_tasks(request.app.state.pool, project_id=project_id)

    @app.post("/api/routines", response_model=RecurringTaskRow)
    async def create_routine_route(
        body: RecurringTaskIn, request: Request, _: None = Depends(auth.verify),
    ):
        pool = request.app.state.pool
        fields = body.model_dump()
        fields["anchor_date"] = fields.get("anchor_date") or _bangkok_today()
        row = await queries.create_recurring_task(pool, fields)
        # Generate-on-save: if it's due today, the instance shows immediately.
        await queries.generate_routines_for(pool, _bangkok_today())
        return row

    @app.patch("/api/routines/{routine_id}", response_model=RecurringTaskRow)
    async def patch_routine_route(
        routine_id: str, body: RecurringTaskPatch, request: Request,
        _: None = Depends(auth.verify),
    ):
        pool = request.app.state.pool
        row = await queries.patch_recurring_task(pool, routine_id, body.model_dump(exclude_unset=True))
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="routine not found")
        await queries.generate_routines_for(pool, _bangkok_today())
        # Keep a synced recurring event in step: paused → remove the series;
        # otherwise refresh it from the new schedule/title.
        if row.get("gcal_event_id") and row.get("gcal_account"):
            cfg = request.app.state.cfg
            try:
                if not row.get("active"):
                    await _unsync_routine_calendar(pool, cfg, row)
                else:
                    await _sync_routine_calendar(pool, cfg, row, row["gcal_account"],
                                                 row.get("gcal_calendar_id"))
                row = await queries.get_recurring_task(pool, routine_id) or row
            except gcal_write.CalendarAuthError:
                log.warning("routine calendar re-sync skipped — not authorized (%s)", routine_id)
            except Exception:
                log.exception("routine calendar re-sync failed for %s", routine_id)
        return row

    @app.delete("/api/routines/{routine_id}", status_code=204)
    async def delete_routine_route(
        routine_id: str, request: Request, _: None = Depends(auth.verify),
    ):
        pool = request.app.state.pool
        routine = await queries.get_recurring_task(pool, routine_id)
        ok = await queries.delete_recurring_task(pool, routine_id)
        if not ok:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="routine not found")
        if routine and routine.get("gcal_event_id") and routine.get("gcal_account"):
            try:
                await _unsync_routine_calendar(pool, request.app.state.cfg, routine)
            except Exception:
                log.warning("could not remove recurring event for deleted routine %s", routine_id)
        return None

    @app.post("/api/routines/generate")
    async def generate_routines_route(request: Request, _: None = Depends(auth.verify)):
        """Materialize today's due routine instances. Idempotent — the daily
        memory-routines.timer hits this; safe to call any number of times."""
        n = await queries.generate_routines_for(request.app.state.pool, _bangkok_today())
        return {"ok": True, "created": n}

    @app.put("/api/routines/{routine_id}/calendar", response_model=RecurringTaskRow)
    async def add_routine_to_calendar_route(
        routine_id: str, body: CalendarTargetIn, request: Request,
        _: None = Depends(auth.verify),
    ):
        pool = request.app.state.pool
        routine = await queries.get_recurring_task(pool, routine_id)
        if routine is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="routine not found")
        try:
            await _sync_routine_calendar(pool, request.app.state.cfg, routine,
                                         body.account.strip().lower(),
                                         (body.calendar_id or "").strip() or None)
        except gcal_write.CalendarAuthError:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                detail=f"{body.account} isn't authorized for calendar writes — re-consent it")
        return await queries.get_recurring_task(pool, routine_id)

    @app.delete("/api/routines/{routine_id}/calendar", response_model=RecurringTaskRow)
    async def remove_routine_from_calendar_route(
        routine_id: str, request: Request, _: None = Depends(auth.verify),
    ):
        pool = request.app.state.pool
        routine = await queries.get_recurring_task(pool, routine_id)
        if routine is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="routine not found")
        await _unsync_routine_calendar(pool, request.app.state.cfg, routine)
        return await queries.get_recurring_task(pool, routine_id)

    # --- task → calendar sync ----------------------------------------

    @app.get("/api/calendars")
    async def list_calendars_route(request: Request, _: None = Depends(auth.verify)):
        """Connected Google accounts a task can be synced to, each with its
        writable calendars (sub-calendar targets). An account whose calendar
        list can't be read (scope/lapse) degrades to primary-only. `default`
        is the configured work calendar."""
        pool = request.app.state.pool
        cfg = request.app.state.cfg
        accounts = await queries.list_calendar_accounts(pool)

        async def cals_for(acct: str) -> list[dict]:
            try:
                return await gcal_write.list_account_calendars(
                    pool, client_secrets_path=cfg.gcal_client_secrets, account_email=acct)
            except Exception:
                return [{"id": "primary", "summary": "Primary", "primary": True}]

        cal_lists = await asyncio.gather(*(cals_for(a["account"]) for a in accounts))
        for a, cals in zip(accounts, cal_lists):
            a["calendars"] = cals
        return {"accounts": accounts, "default": cfg.work_calendar_account}

    @app.put("/api/tasks/{task_id}/calendar", response_model=TaskRow)
    async def add_task_to_calendar_route(
        task_id: str, body: CalendarTargetIn, request: Request,
        _: None = Depends(auth.verify),
    ):
        pool = request.app.state.pool
        task = await queries.get_task(pool, task_id)
        if task is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="task not found")
        try:
            await _sync_task_calendar(pool, request.app.state.cfg, task,
                                      body.account.strip().lower(),
                                      (body.calendar_id or "").strip() or None)
        except gcal_write.CalendarAuthError:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                detail=f"{body.account} isn't authorized for calendar writes — re-consent it")
        return await queries.get_task(pool, task_id)

    @app.delete("/api/tasks/{task_id}/calendar", response_model=TaskRow)
    async def remove_task_from_calendar_route(
        task_id: str, request: Request, _: None = Depends(auth.verify),
    ):
        pool = request.app.state.pool
        task = await queries.get_task(pool, task_id)
        if task is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="task not found")
        await _unsync_task_calendar(pool, request.app.state.cfg, task)
        return await queries.get_task(pool, task_id)

    # --- opportunities -----------------------------------------------

    # --- deal-stage config (self-serve stage manager) -------------------

    @app.get("/api/stages", response_model=list[StageConfigRow])
    async def list_stages_route(request: Request, _: None = Depends(auth.verify)):
        """All configured deal stages in board order (with usage counts)."""
        return await queries.list_stages(request.app.state.pool)

    @app.post("/api/stages", response_model=StageConfigRow)
    async def create_stage_route(
        body: StageCreateIn, request: Request, _: None = Depends(auth.verify),
    ):
        return await queries.create_stage(
            request.app.state.pool, label=body.label, color=body.color,
            terminal=body.terminal, closes=body.closes,
        )

    @app.put("/api/stages/reorder", response_model=list[StageConfigRow])
    async def reorder_stages_route(
        body: StageReorderIn, request: Request, _: None = Depends(auth.verify),
    ):
        """Set board order from the given key list. Registered BEFORE the
        /api/stages/{key} routes so 'reorder' isn't captured as a key."""
        return await queries.reorder_stages(request.app.state.pool, body.keys)

    @app.patch("/api/stages/{key}", response_model=StageConfigRow)
    async def patch_stage_route(
        key: str, body: StagePatchIn, request: Request, _: None = Depends(auth.verify),
    ):
        row = await queries.update_stage(
            request.app.state.pool, key, label=body.label, color=body.color,
            terminal=body.terminal, closes=body.closes,
        )
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="stage not found")
        return row

    @app.delete("/api/stages/{key}", status_code=204)
    async def delete_stage_route(
        key: str, request: Request, _: None = Depends(auth.verify),
    ):
        err = await queries.delete_stage(request.app.state.pool, key)
        if err == "not_found":
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="stage not found")
        if err == "in_use":
            raise HTTPException(status.HTTP_409_CONFLICT,
                                detail="stage has deals — move them first")
        if err == "last_stage":
            raise HTTPException(status.HTTP_409_CONFLICT,
                                detail="cannot delete the last live stage")

    @app.get("/api/opportunities", response_model=list[OpportunityRow])
    async def list_opps_route(
        request: Request,
        project_id: str | None = None,
        stage: str | None = None,
        counterparty_id: str | None = None,
        q: str | None = None,
        tags: str | None = None,
        limit: int = 50, offset: int = 0,
        _: None = Depends(auth.verify),
    ):
        """`tags` is a comma-separated list; a deal matches if it carries ANY of
        them (overlap), so 'job,consulting' shows both streams."""
        limit = max(1, min(limit, 200))
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
        return await queries.list_opportunities(
            request.app.state.pool,
            project_id=project_id, stage=stage,
            counterparty_id=counterparty_id, q=q,
            limit=limit, offset=offset, tags=tag_list,
        )

    @app.get("/api/opportunities/tags", response_model=list[str])
    async def list_opp_tags_route(request: Request, _: None = Depends(auth.verify)):
        """Tag vocabulary in use — powers the filter bar. Registered BEFORE
        /api/opportunities/{opp_id} so the literal 'tags' path isn't shadowed."""
        return await queries.list_opportunity_tags(request.app.state.pool)

    @app.post("/api/opportunities", response_model=OpportunityRow)
    async def create_opp_route(
        body: OpportunityCreate, request: Request, _: None = Depends(auth.verify),
    ):
        return await queries.create_opportunity(
            request.app.state.pool, body.model_dump(exclude_unset=False),
        )

    @app.patch("/api/opportunities/{opp_id}", response_model=OpportunityRow)
    async def patch_opp_route(
        opp_id: str, body: OpportunityPatch, request: Request,
        _: None = Depends(auth.verify),
    ):
        fields = body.model_dump(exclude_unset=True)
        row = await queries.patch_opportunity(request.app.state.pool, opp_id, fields)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="opportunity not found")
        return row

    @app.delete("/api/opportunities/{opp_id}", status_code=204)
    async def delete_opp_route(
        opp_id: str, request: Request, _: None = Depends(auth.verify),
    ):
        ok = await queries.soft_delete_opportunity(request.app.state.pool, opp_id)
        if not ok:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="opportunity not found")
        return None

    @app.get("/api/focus")
    async def focus_route(
        request: Request, limit: int = 15, _: None = Depends(auth.verify),
    ):
        """Ranked next-actions across open tasks + live deals (deterministic
        score, with a reason per item)."""
        limit = max(1, min(limit, 50))
        return await queries.focus_items(request.app.state.pool, limit=limit)

    @app.get("/api/pipeline")
    async def pipeline_route(
        request: Request, project_id: str | None = None,
        _: None = Depends(auth.verify),
    ):
        """Live-deal funnel: per-stage count + summed award_usd + totals.
        Top-level path (not /api/opportunities/...) to avoid colliding with
        the /{opp_id} detail route."""
        return await queries.pipeline_summary(request.app.state.pool, project_id=project_id)

    @app.get("/api/opportunities/{opp_id}", response_model=OpportunityDetail)
    async def get_opp_route(
        opp_id: str, request: Request, _: None = Depends(auth.verify),
    ):
        row = await queries.get_opportunity_detail(request.app.state.pool, opp_id)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="opportunity not found")
        return row

    @app.post("/api/opportunities/{opp_id}/stage", response_model=OpportunityDetail)
    async def change_opp_stage_route(
        opp_id: str, body: OpportunityStageIn, request: Request,
        _: None = Depends(auth.verify),
    ):
        """Move the deal to a new stage and log it on the timeline with the
        next step. The single entry point for stage changes (records history)."""
        try:
            row = await queries.change_opportunity_stage(
                request.app.state.pool, opp_id,
                stage=body.stage, next_step=body.next_step, note=body.note,
            )
        except ValueError as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="opportunity not found")
        return row

    @app.post("/api/opportunities/{opp_id}/events", response_model=OpportunityDetail)
    async def add_opp_event_route(
        opp_id: str, body: OpportunityEventIn, request: Request,
        _: None = Depends(auth.verify),
    ):
        """Append a freeform note / next-step to the timeline (no stage change)."""
        row = await queries.add_opportunity_event(
            request.app.state.pool, opp_id,
            next_step=body.next_step, note=body.note,
        )
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="opportunity not found")
        return row

    # --- companies (Phase 6) -------------------------------------------

    @app.get("/api/companies", response_model=list[CompanyRow])
    async def list_companies_route(
        request: Request, q: str | None = None, limit: int = 100, offset: int = 0,
        _: None = Depends(auth.verify),
    ):
        limit = max(1, min(limit, 500))
        viewer = await auth.resolve_viewer(request)   # member → reduced, shared/own only
        return await queries.list_companies(request.app.state.pool, q=q, limit=limit, offset=offset, viewer=viewer)

    @app.post("/api/companies", response_model=CompanyRow)
    async def create_company_route(
        body: CompanyCreate, request: Request, _: None = Depends(auth.verify),
    ):
        row = await queries.create_company(request.app.state.pool, body.model_dump(exclude_unset=False))
        # return list-shaped row with counts
        rows = await queries.list_companies(request.app.state.pool, q=None, limit=500, offset=0)
        for r in rows:
            if r["id"] == row["id"]:
                return r
        return {**row, "people_count": 0, "live_opp_count": 0, "pipeline_usd": 0.0}

    # LinkedIn link-review queue. Registered BEFORE /api/companies/{company_id}
    # so the literal 'link-suggestions' segment isn't swallowed by the UUID route.
    @app.get("/api/companies/link-suggestions")
    async def list_link_suggestions_route(
        request: Request, limit: int = 100, _: None = Depends(auth.verify),
    ):
        limit = max(1, min(limit, 500))
        return await queries.list_link_suggestions(request.app.state.pool, limit=limit)

    @app.get("/api/companies/link-suggestions/count")
    async def count_link_suggestions_route(
        request: Request, _: None = Depends(auth.verify),
    ):
        return {"count": await queries.count_link_suggestions(request.app.state.pool)}

    @app.post("/api/companies/link-suggestions/dismiss", status_code=204)
    async def dismiss_link_suggestion_route(
        body: LinkSuggestionDismissIn, request: Request, _: None = Depends(auth.verify),
    ):
        await queries.dismiss_link_suggestion(
            request.app.state.pool, body.person_id, body.company_id
        )

    @app.get("/api/companies/{company_id}", response_model=CompanyDetail)
    async def get_company_route(
        company_id: str, request: Request, _: None = Depends(auth.verify),
    ):
        row = await queries.get_company_detail(request.app.state.pool, company_id)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="company not found")
        return row

    @app.patch("/api/companies/{company_id}", response_model=CompanyDetail)
    async def patch_company_route(
        company_id: str, body: CompanyPatch, request: Request, _: None = Depends(auth.verify),
    ):
        row = await queries.patch_company(request.app.state.pool, company_id, body.model_dump(exclude_unset=True))
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="company not found")
        return await queries.get_company_detail(request.app.state.pool, company_id)

    @app.patch("/api/companies/{company_id}/sharing", response_model=CompanyDetail)
    async def set_company_sharing_route(
        company_id: str, body: ContactSharing, request: Request, role: str = Depends(auth.verify),
    ):
        """Owner action: share/unshare a company with members."""
        if role != "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="owner only")
        ok = await queries.set_company_sharing(
            request.app.state.pool, company_id, visibility=body.visibility, owner_member_id=body.owner_member_id)
        if not ok:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="company not found")
        return await queries.get_company_detail(request.app.state.pool, company_id)

    @app.delete("/api/companies/{company_id}", status_code=204)
    async def delete_company_route(
        company_id: str, request: Request, _: None = Depends(auth.verify),
    ):
        ok = await queries.soft_delete_company(request.app.state.pool, company_id)
        if not ok:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="company not found")
        return None

    @app.post("/api/companies/{company_id}/people", status_code=204)
    async def add_company_person_route(
        company_id: str, body: CompanyPersonIn, request: Request, _: None = Depends(auth.verify),
    ):
        ok = await queries.add_company_person(
            request.app.state.pool, company_id, body.person_id, body.role, body.is_current,
        )
        if not ok:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="could not add person")
        return None

    @app.delete("/api/companies/{company_id}/people/{person_id}", status_code=204)
    async def remove_company_person_route(
        company_id: str, person_id: str, request: Request, _: None = Depends(auth.verify),
    ):
        ok = await queries.remove_company_person(request.app.state.pool, company_id, person_id)
        if not ok:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="person not on company")
        return None

    @app.post("/api/companies/{company_id}/merge")
    async def merge_company_route(
        company_id: str, body: CompanyMergeIn, request: Request, _: None = Depends(auth.verify),
    ):
        """Fold this company into into_id (moves people + deals, soft-deletes this one)."""
        ok = await queries.merge_companies(request.app.state.pool, company_id, body.into_id)
        if not ok:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="merge failed (same id or company not found)")
        return {"ok": True}

    # --- mail (read-only Gmail reader — backlog #2 Phase 1; admin-only via the
    # role middleware: budget callers never reach /api/mail/*) ---
    @app.get("/api/mail/accounts", response_model=list[MailAccount])
    async def mail_accounts_route(request: Request, _: None = Depends(auth.verify)):
        return await queries.list_mail_accounts(request.app.state.pool)

    @app.get("/api/mail/threads", response_model=list[MailThreadRow])
    async def mail_threads_route(
        request: Request, q: str | None = None, account: str | None = None,
        category: str | None = None, archived: str = "hide", starred: bool = False,
        content: str | None = None, trashed: str = "hide", snoozed: str = "hide",
        limit: int = 50, offset: int = 0, _: None = Depends(auth.verify),
    ):
        return await queries.list_mail_threads(
            request.app.state.pool, q=q, account=account, category=category,
            archived=(archived if archived in ("hide", "only", "all") else "hide"),
            starred=starred,
            content=(content if content in ("newsletter", "transactional", "personal") else None),
            trashed=(trashed if trashed in ("hide", "only", "all") else "hide"),
            snoozed=(snoozed if snoozed in ("hide", "only", "all") else "hide"),
            limit=max(1, min(limit, 200)), offset=max(0, offset))

    @app.post("/api/mail/thread/state")
    async def mail_thread_state_route(body: MailStateIn, request: Request, _: None = Depends(auth.verify)):
        """Set a thread's local archive/star state, then push it back to Gmail
        (Phase 3c). The local overlay is the source of truth and always saves; the
        Gmail push is best-effort — if the account lacks the gmail.modify scope the
        response carries gmail_synced=false so the UI can prompt a re-consent.
        thread_key == the Gmail threadId when a thread_id exists (it's
        COALESCE(thread_id, message_id)); push_thread_state falls back to a
        per-message modify otherwise."""
        pool, cfg = request.app.state.pool, request.app.state.cfg
        state = await queries.set_mail_state(
            pool, account_email=body.account_email,
            thread_key=body.thread_key, archived=body.archived, starred=body.starred,
            read=body.read, trashed=body.trashed, snoozed_until=body.snoozed_until,
            set_snooze=("snoozed_until" in body.model_fields_set))
        gmail_synced, gmail_reason = True, None
        try:
            await gmail_fetch.push_thread_state(
                pool, client_secrets_path=cfg.gcal_client_secrets,
                account_email=body.account_email, gmail_thread_id=body.thread_key,
                archived=body.archived, starred=body.starred, trashed=body.trashed)
        except gmail_fetch.GmailModifyError as e:
            gmail_synced, gmail_reason = False, "needs gmail.modify re-consent"
            log.info("mail state saved locally, Gmail push skipped: %s", e)
        except Exception as e:  # noqa: BLE001
            gmail_synced, gmail_reason = False, "gmail sync error"
            log.exception("mail state Gmail push failed: %s", e)
        return {**state, "gmail_synced": gmail_synced, "gmail_reason": gmail_reason}

    @app.post("/api/mail/thread/class")
    async def mail_thread_class_route(body: MailClassIn, request: Request,
                                      _: None = Depends(auth.verify)):
        """User correction of a thread's content class (triage iteration).
        Stamps every message in the thread as model_version='user' ground truth —
        feeds the retrain eval metric and the labeling-rule tuning recipe
        (scripts/mail_class_corrections.sql). App-local; nothing goes to Gmail."""
        n = await queries.set_mail_class_user(
            request.app.state.pool, account_email=body.account_email,
            thread_key=body.thread_key, content_class=body.content_class)
        return {"thread_key": body.thread_key, "content_class": body.content_class,
                "messages": n}

    @app.get("/api/mail/senders", response_model=list[MailSenderRow])
    async def mail_senders_route(
        request: Request, account: str | None = None, q: str | None = None,
        limit: int = 100, _: None = Depends(auth.verify),
    ):
        """Mail grouped by sender, highest volume first (round 2 / P3) — the
        fast-cleanup view behind the sidebar 'Senders' pane."""
        return await queries.list_mail_senders(
            request.app.state.pool, account=account, q=q, limit=max(1, min(limit, 300)))

    @app.post("/api/mail/sender/act")
    async def mail_sender_act_route(body: MailSenderActIn, request: Request,
                                    _: None = Depends(auth.verify)):
        """Bulk action on everything from one sender. Local overlay first (always
        succeeds), then a per-account Gmail batchModify push (best-effort):
          read    → overlay read=true;     Gmail remove UNREAD
          archive → overlay archived=true; Gmail remove INBOX
          trash   → overlay trashed=true;  Gmail add TRASH (recoverable 30 days)
        """
        pool, cfg = request.app.state.pool, request.app.state.cfg
        overlay = _SENDER_ACT_OVERLAY[body.action]
        labels = _SENDER_ACT_LABELS[body.action]
        threads = await queries.set_sender_state(
            pool, from_address=body.from_address, account=body.account, **overlay)
        pushed, errors = 0, []
        by_account = await queries.sender_message_ids(
            pool, from_address=body.from_address, account=body.account)
        for acct, ids in by_account.items():
            try:
                pushed += await gmail_fetch.batch_modify(
                    pool, client_secrets_path=cfg.gcal_client_secrets,
                    account_email=acct, message_ids=ids,
                    add=labels[0], remove=labels[1])
            except gmail_fetch.GmailModifyError as e:
                errors.append(f"{acct}: {e}")
                log.info("sender %s push skipped for %s: %s", body.action, acct, e)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{acct}: {e}")
                log.warning("sender %s push failed for %s: %s", body.action, acct, e)
        return {"threads": threads, "gmail_pushed": pushed,
                "gmail_errors": errors or None}

    @app.get("/api/mail/cleanup/senders", response_model=list[MailCleanupSenderRow])
    async def mail_cleanup_senders_route(
        request: Request, account: str | None = None, q: str | None = None,
        limit: int = 200, _: None = Depends(auth.verify),
    ):
        """The Mail-Cleanup page's sender list — volume-ranked with unsubscribe /
        one-click / content-class / replied signals for the recommendations."""
        return await queries.list_mail_cleanup_senders(
            request.app.state.pool, account=account, q=q, limit=max(1, min(limit, 500)))

    @app.post("/api/mail/senders/bulk-act")
    async def mail_senders_bulk_act_route(body: MailBulkActIn, request: Request,
                                          _: None = Depends(auth.verify)):
        """One bulk action across MANY senders — the cleanup page's 'trash
        selected'. Applies the local overlay + a per-account Gmail batchModify for
        each sender; best-effort, mirrors /sender/act's semantics."""
        pool, cfg = request.app.state.pool, request.app.state.cfg
        overlay = _SENDER_ACT_OVERLAY[body.action]
        add, remove = _SENDER_ACT_LABELS[body.action]
        threads_total, pushed_total, errors = 0, 0, []
        for addr in body.from_addresses:
            threads_total += await queries.set_sender_state(
                pool, from_address=addr, account=body.account, **overlay)
            by_account = await queries.sender_message_ids(
                pool, from_address=addr, account=body.account)
            for acct, ids in by_account.items():
                try:
                    pushed_total += await gmail_fetch.batch_modify(
                        pool, client_secrets_path=cfg.gcal_client_secrets,
                        account_email=acct, message_ids=ids, add=add, remove=remove)
                except gmail_fetch.GmailModifyError as e:
                    errors.append(f"{addr} @ {acct}: {e}")
                    log.info("bulk %s push skipped for %s/%s: %s", body.action, addr, acct, e)
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{addr} @ {acct}: {e}")
                    log.warning("bulk %s push failed for %s/%s: %s", body.action, addr, acct, e)
        return {"senders": len(body.from_addresses), "threads": threads_total,
                "gmail_pushed": pushed_total, "gmail_errors": errors or None}

    @app.post("/api/mail/sender/unsubscribe")
    async def mail_sender_unsubscribe_route(body: MailSenderUnsubIn, request: Request,
                                            _: None = Depends(auth.verify)):
        """Unsubscribe from a sender. When it offers RFC 8058 one-click, POST the
        target server-side (silent). Otherwise return the https link / mailto for
        the user to finish — we never GET-fetch a link blindly (can be a tracker)."""
        tgt = await queries.sender_unsubscribe_target(
            request.app.state.pool, from_address=body.from_address, account=body.account)
        if not tgt or not (tgt["https"] or tgt["mailto"]):
            return {"ok": False, "method": "none", "url": None}
        if tgt["one_click"] and tgt["https"]:
            try:
                async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as c:
                    r = await c.post(
                        tgt["https"], content=b"List-Unsubscribe=One-Click",
                        headers={"Content-Type": "application/x-www-form-urlencoded"})
                return {"ok": r.status_code < 400, "method": "one-click",
                        "url": tgt["https"], "status": r.status_code}
            except Exception as e:  # noqa: BLE001
                log.info("one-click unsubscribe failed for %s: %s", body.from_address, e)
                return {"ok": False, "method": "one-click", "url": tgt["https"], "error": str(e)}
        return {"ok": False, "method": "link" if tgt["https"] else "mailto",
                "url": tgt["https"] or tgt["mailto"]}

    @app.post("/api/mail/sender/keep")
    async def mail_sender_keep_route(body: MailSenderKeepIn, request: Request,
                                     _: None = Depends(auth.verify)):
        """Pin/unpin a sender as 'keep' — excluded from cleanup recommendations
        and never auto-selected by 'Select recommended'."""
        pref = await queries.set_mail_sender_pref(
            request.app.state.pool, from_address=body.from_address, keep=body.keep)
        return {"from_address": body.from_address, "kept": pref["keep"], "on_clear_list": pref["clear"]}

    @app.post("/api/mail/sender/clear")
    async def mail_sender_clear_route(body: MailSenderClearIn, request: Request,
                                      _: None = Depends(auth.verify)):
        """Add/remove a sender on the clear list (bulk-trash later). Mutually
        exclusive with 'keep'. Callable from the message view while reading junk."""
        pref = await queries.set_mail_sender_pref(
            request.app.state.pool, from_address=body.from_address, clear=body.clear)
        return {"from_address": body.from_address, "on_clear_list": pref["clear"], "kept": pref["keep"]}

    @app.get("/api/mail/cleanup/clear-list", response_model=list[MailCleanupSenderRow])
    async def mail_clear_list_route(request: Request, account: str | None = None,
                                    _: None = Depends(auth.verify)):
        """Senders on the clear list — the batch flagged from the message view,
        with remaining counts, ready to bulk-trash."""
        return await queries.list_mail_clear_senders(request.app.state.pool, account=account)

    @app.post("/api/mail/mark-all-read")
    async def mail_mark_all_read_route(
        request: Request, account: str | None = None, _: None = Depends(auth.verify),
    ):
        """Mark every currently-unread thread read (app-local overlay), as of now."""
        n = await queries.mark_all_mail_read(request.app.state.pool, account=account)
        return {"marked": n}

    @app.get("/api/mail/thread", response_model=list[MailMessage])
    async def mail_thread_route(
        request: Request, key: str, account: str | None = None,
        _: None = Depends(auth.verify),
    ):
        pool = request.app.state.pool
        msgs = await queries.get_mail_thread(pool, key, account)
        if not msgs:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="thread not found")
        # Backfill attachment/header metadata for pre-Phase-3b/B messages in the
        # BACKGROUND (P1 speed): the response returns stored data immediately instead
        # of awaiting Gmail; enrichment lands in payload for the next open.
        stale = [{"account_email": m["account_email"], "message_id": m["message_id"],
                  "_attachments_known": m.get("_attachments_known"),
                  "_headers_known": m.get("_headers_known")}
                 for m in msgs if not m.get("_attachments_known") or not m.get("_headers_known")]
        if stale:
            async def _bg_backfill():
                try:
                    await gmail_fetch.backfill_thread_metadata(
                        pool, client_secrets_path=request.app.state.cfg.gcal_client_secrets,
                        messages=stale)
                except Exception:  # noqa: BLE001
                    log.exception("background metadata backfill failed")
            asyncio.create_task(_bg_backfill())
        for m in msgs:
            m.pop("_attachments_known", None)
            m.pop("_headers_known", None)
        return msgs

    @app.get("/api/mail/attachment")
    async def mail_attachment_route(
        request: Request, account: str, message_id: str, attachment_id: str,
        filename: str = "attachment", _: None = Depends(auth.verify),
    ):
        """Download one attachment's bytes on demand (Phase 3b). Uses the account's
        existing gmail.readonly grant — no re-consent needed."""
        cfg = request.app.state.cfg
        try:
            data = await gmail_fetch.download_attachment(
                request.app.state.pool, client_secrets_path=cfg.gcal_client_secrets,
                account_email=account, message_id=message_id, attachment_id=attachment_id)
        except gmail_fetch.GmailReadError as e:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                detail=f"cannot read attachment for {account}: {e}")
        except Exception as e:  # noqa: BLE001
            log.exception("attachment download failed")
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"attachment error: {e}")
        safe = filename.replace('"', "").replace("\\", "").replace("\n", "").replace("\r", "")
        return Response(
            content=data, media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{safe}"'})

    @app.post("/api/mail/scan-spam")
    async def mail_scan_spam_route(
        request: Request, account: str | None = None, limit: int = 200,
        _: None = Depends(auth.verify),
    ):
        """Batch-score recent unscored messages with Rspamd (Phase B2): fetch each
        raw RFC822 (gmail.readonly), POST to Rspamd /checkv2, persist the verdict.
        Bounded per call; re-runs only cover new mail. Returns {scanned, spam, errors}."""
        cfg, pool = request.app.state.cfg, request.app.state.pool
        if not cfg.rspamd_url:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="rspamd not configured")
        todo = await queries.list_unscored_messages(pool, account=account, limit=max(1, min(limit, 500)))
        if not todo:
            return {"scanned": 0, "spam": 0, "errors": 0}

        sem = asyncio.Semaphore(5)
        scanned = spam = errors = 0

        # One token refresh per account, not per message — a per-message refresh
        # under concurrency storms DNS (transient oauth2.googleapis.com failures).
        by_account: dict[str, list[dict]] = {}
        for m in todo:
            by_account.setdefault(m["account_email"], []).append(m)
        creds_by_account: dict = {}
        for acct, msgs in by_account.items():
            try:
                creds_by_account[acct] = await gmail_fetch.account_credentials(
                    pool, client_secrets_path=cfg.gcal_client_secrets, account_email=acct)
            except gmail_fetch.GmailReadError as e:
                errors += len(msgs)
                log.info("spam scan: account %s unusable, %d skipped: %s", acct, len(msgs), e)
            except Exception as e:  # noqa: BLE001 — transient (e.g. DNS); skip account this run
                errors += len(msgs)
                log.warning("spam scan: creds for %s failed, %d skipped: %s", acct, len(msgs), e)
        todo = [m for m in todo if m["account_email"] in creds_by_account]

        async with httpx.AsyncClient(timeout=30) as client:
            async def _one(msg: dict):
                nonlocal scanned, spam, errors
                async with sem:
                    try:
                        raw = await gmail_fetch.fetch_raw_message(
                            pool, client_secrets_path=cfg.gcal_client_secrets,
                            account_email=msg["account_email"], message_id=msg["message_id"],
                            creds=creds_by_account[msg["account_email"]])
                        verdict = await rspamd.check(raw, url=cfg.rspamd_url, client=client)
                    except gmail_fetch.GmailGoneError:
                        # deleted in Gmail — store a terminal 'gone' verdict so the
                        # unscored queue stops re-fetching it every hour
                        await queries.upsert_mail_spam(
                            pool, account_email=msg["account_email"],
                            message_id=msg["message_id"], score=None, action="gone", symbols={})
                        return
                    except (gmail_fetch.GmailReadError, rspamd.RspamdError) as e:
                        errors += 1
                        log.info("spam scan skipped %s: %s", msg["message_id"], e)
                        return
                    except Exception as e:  # noqa: BLE001
                        errors += 1
                        log.warning("spam scan error %s: %s", msg["message_id"], e)
                        return
                    await queries.upsert_mail_spam(
                        pool, account_email=msg["account_email"], message_id=msg["message_id"],
                        score=verdict["score"], action=verdict["action"], symbols=verdict["symbols"])
                    scanned += 1
                    if rspamd.is_spammy(verdict["action"]):
                        spam += 1

            await asyncio.gather(*(_one(m) for m in todo))
        return {"scanned": scanned, "spam": spam, "errors": errors}

    @app.post("/api/mail/train-bayes")
    async def mail_train_bayes_route(
        request: Request, account: str | None = None, limit: int = 200,
        _: None = Depends(auth.verify),
    ):
        """Feed Rspamd's Bayes classifier (triage iteration). Spam corpus is
        listed LIVE from each account's Gmail spam folder (the fetcher never
        ingests spam — Gmail's own verdicts, ~30-day retention); ham corpus is
        our scored-clean mail (personal/read first). Every learned message is
        recorded in memory.mail_bayes_learn so re-runs only feed new mail.
        Balanced by plan_learn_batch (min_learns is per-class)."""
        pool, cfg = request.app.state.pool, request.app.state.cfg
        if not cfg.rspamd_controller_url:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                detail="RSPAMD_CONTROLLER_URL not configured")
        limit = max(1, min(limit, 500))

        learned_totals = await queries.count_bayes_learned(pool)

        # spam side: live Gmail spam-folder listing per account, minus learned.
        # creds_cache holds one refreshed Credentials per account for the whole
        # run (listing + learn batch) — refreshing per message storms DNS.
        spam_pairs: list[tuple[str, str]] = []
        creds_cache: dict = {}
        errors = 0
        for acct in await queries.list_active_mail_accounts(pool, account=account):
            try:
                creds_cache[acct] = await gmail_fetch.account_credentials(
                    pool, client_secrets_path=cfg.gcal_client_secrets, account_email=acct)
                ids = await gmail_fetch.list_spam_message_ids(
                    pool, client_secrets_path=cfg.gcal_client_secrets,
                    account_email=acct, limit=limit, creds=creds_cache[acct])
            except gmail_fetch.GmailReadError as e:
                errors += 1
                log.info("bayes spam listing skipped for %s: %s", acct, e)
                continue
            except Exception as e:  # noqa: BLE001 — e.g. transient DNS to oauth2;
                # a flaky account listing must not 500 the whole feeder run
                errors += 1
                log.warning("bayes spam listing failed for %s: %s", acct, e)
                continue
            fresh = await queries.filter_unlearned(pool, account_email=acct, message_ids=ids)
            spam_pairs.extend((acct, mid) for mid in fresh)

        ham_rows = await queries.list_bayes_ham_candidates(pool, account=account, limit=limit)
        ham_pairs = [(r["account_email"], r["message_id"]) for r in ham_rows]

        n_spam, n_ham = rspamd.plan_learn_batch(
            len(spam_pairs), len(ham_pairs), limit=limit,
            learned_spam=learned_totals["spam"], learned_ham=learned_totals["ham"])
        batch = [("spam", a, m) for a, m in spam_pairs[:n_spam]] \
              + [("ham", a, m) for a, m in ham_pairs[:n_ham]]
        if not batch:
            return {"learned_spam": 0, "learned_ham": 0, "already": 0, "errors": errors,
                    "total_spam": learned_totals["spam"], "total_ham": learned_totals["ham"]}

        # ham candidates can span accounts the spam loop never got creds for
        # (listing failed, or ham-only) — build the missing ones once, up front
        for acct in sorted({a for _, a, _ in batch} - creds_cache.keys()):
            n = sum(1 for _, a, _ in batch if a == acct)
            try:
                creds_cache[acct] = await gmail_fetch.account_credentials(
                    pool, client_secrets_path=cfg.gcal_client_secrets, account_email=acct)
            except gmail_fetch.GmailReadError as e:
                errors += n
                log.info("bayes learn: account %s unusable, %d skipped: %s", acct, n, e)
            except Exception as e:  # noqa: BLE001
                errors += n
                log.warning("bayes learn: creds for %s failed, %d skipped: %s", acct, n, e)
        batch = [t for t in batch if t[1] in creds_cache]

        sem = asyncio.Semaphore(5)
        counts = {"spam": 0, "ham": 0, "already": 0}

        async with httpx.AsyncClient(timeout=30) as client:
            async def _one(kind: str, acct: str, message_id: str):
                nonlocal errors
                async with sem:
                    try:
                        raw = await gmail_fetch.fetch_raw_message(
                            pool, client_secrets_path=cfg.gcal_client_secrets,
                            account_email=acct, message_id=message_id,
                            creds=creds_cache[acct])
                        outcome = await rspamd.learn(
                            kind, raw, url=cfg.rspamd_controller_url, client=client)
                    except gmail_fetch.GmailGoneError:
                        # deleted in Gmail — ledger it (nothing was learned, but
                        # retrying can never succeed; keeps the candidate queue clean)
                        counts["already"] += 1
                        await queries.record_bayes_learn(
                            pool, account_email=acct, message_id=message_id, learned_as=kind)
                        return
                    except (gmail_fetch.GmailReadError, rspamd.RspamdError) as e:
                        errors += 1
                        log.info("bayes learn skipped %s/%s: %s", kind, message_id, e)
                        return
                    except Exception as e:  # noqa: BLE001
                        errors += 1
                        log.warning("bayes learn error %s/%s: %s", kind, message_id, e)
                        return
                    counts["already" if outcome == "already" else kind] += 1
                    await queries.record_bayes_learn(
                        pool, account_email=acct, message_id=message_id, learned_as=kind)

            await asyncio.gather(*(_one(k, a, m) for k, a, m in batch))

        return {"learned_spam": counts["spam"], "learned_ham": counts["ham"],
                "already": counts["already"], "errors": errors,
                "total_spam": learned_totals["spam"] + counts["spam"],
                "total_ham": learned_totals["ham"] + counts["ham"]}

    @app.post("/api/mail/backfill-headers")
    async def mail_backfill_headers_route(
        request: Request, account: str | None = None, limit: int = 500,
        _: None = Depends(auth.verify),
    ):
        """Bulk-backfill triage headers (Phase B) for messages ingested before it —
        so the auto/bulk/list chips + unsubscribe links show on old mail without
        opening each thread. Reuses the lazy on-open enrichment; bounded + re-runnable."""
        pool, cfg = request.app.state.pool, request.app.state.cfg
        todo = await queries.list_unheadered_messages(pool, account=account, limit=max(1, min(limit, 2000)))
        if not todo:
            return {"backfilled": 0, "remaining_batch": 0}
        try:
            await gmail_fetch.backfill_thread_metadata(
                pool, client_secrets_path=cfg.gcal_client_secrets, messages=todo)
        except Exception as e:  # noqa: BLE001
            log.exception("header backfill failed")
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"backfill error: {e}")
        # backfill sets m["signals"] on each message it successfully enriched.
        done = sum(1 for m in todo if "signals" in m)
        return {"found_unheadered": len(todo), "backfilled": done}

    @app.post("/api/mail/send")
    async def mail_send_route(body: MailSendIn, request: Request, _: None = Depends(auth.verify)):
        """Send a new mail or a threaded reply from `account_email` (Phase 2).
        The account must be re-consented with the gmail.send scope."""
        cfg = request.app.state.cfg
        if not body.to.strip():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="recipient required")
        try:
            res = await gmail_send.send_email(
                request.app.state.pool, client_secrets_path=cfg.gcal_client_secrets,
                account_email=body.account_email, to_email=body.to.strip(),
                subject=body.subject, body=body.body, html=body.html,
                in_reply_to=body.in_reply_to, references=body.references, thread_id=body.thread_id)
        except gmail_send.GmailAuthError:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                detail=f"{body.account_email} not authorized for gmail.send — re-consent it")
        except Exception as e:  # noqa: BLE001
            log.exception("mail send failed")
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"send error: {e}")
        return {"ok": True, "message_id": res.get("message_id")}

    @app.get("/api/telegram/groups")
    async def list_telegram_groups_route(
        request: Request,
        q: str | None = None,
        kind: str | None = None,            # 'group' | 'supergroup' | 'channel'
        enabled: bool | None = None,
        min_members: int | None = None,
        max_members: int | None = None,
        sort: str = queries.TELEGRAM_GROUPS_SORT_DEFAULT,
        limit: int = 50,
        offset: int = 0,
        _: None = Depends(auth.verify),
    ):
        pool = request.app.state.pool
        limit = max(1, min(limit, 200))
        return await queries.list_telegram_groups(
            pool, q=q, kind=kind, enabled=enabled,
            min_members=min_members, max_members=max_members,
            sort=sort, limit=limit, offset=offset,
        )

    @app.get("/api/telegram/groups/count")
    async def count_telegram_groups_route(
        request: Request,
        q: str | None = None,
        kind: str | None = None,
        enabled: bool | None = None,
        min_members: int | None = None,
        max_members: int | None = None,
        _: None = Depends(auth.verify),
    ):
        pool = request.app.state.pool
        return {"count": await queries.count_telegram_groups(
            pool, q=q, kind=kind, enabled=enabled,
            min_members=min_members, max_members=max_members,
        )}

    # group follow suggestions (backlog #3) — literal paths BEFORE /{chat_id}
    @app.get("/api/telegram/groups/suggestions")
    async def tg_group_suggestions_route(request: Request, limit: int = 20, _: None = Depends(auth.verify)):
        return await queries.list_group_suggestions(request.app.state.pool, limit=max(1, min(limit, 100)))

    @app.get("/api/telegram/groups/suggestions/count")
    async def tg_group_suggestions_count_route(request: Request, _: None = Depends(auth.verify)):
        return {"count": await queries.count_group_suggestions(request.app.state.pool)}

    @app.post("/api/telegram/groups/{chat_id}/dismiss", status_code=204)
    async def tg_group_dismiss_route(chat_id: int, request: Request, _: None = Depends(auth.verify)):
        if not await queries.dismiss_group_suggestion(request.app.state.pool, chat_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="group not found")

    @app.get("/api/telegram/groups/{chat_id}")
    async def get_telegram_group_route(
        chat_id: int, request: Request,
        limit: int = 50, offset: int = 0,
        _: None = Depends(auth.verify),
    ):
        """Group detail: metadata + top-20 senders + paginated message
        stream. Used by the /groups/{chat_id} UI page."""
        pool = request.app.state.pool
        limit = max(1, min(limit, 200))
        out = await queries.get_group_detail(
            pool, chat_id=chat_id, recent_limit=limit, recent_offset=offset,
        )
        if out is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="group not in allowlist")
        return out

    @app.post("/api/telegram/groups/{chat_id}/toggle")
    async def toggle_telegram_group_route(
        chat_id: int, request: Request, _: None = Depends(auth.verify),
    ):
        """Toggle the enabled flag. Body optional `{enabled: bool}`; if
        omitted the current value flips."""
        pool = request.app.state.pool
        # Read current → decide new value
        async with pool.acquire() as conn:
            current = await conn.fetchrow(
                "SELECT enabled FROM raw.telegram_group_allowlist WHERE chat_id = $1",
                chat_id,
            )
        if current is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="group not in allowlist")
        try:
            body = await request.json()
        except Exception:
            body = {}
        new_value = bool(body.get("enabled")) if "enabled" in (body or {}) else not current["enabled"]
        ok = await queries.toggle_telegram_group(pool, chat_id, new_value)
        if not ok:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="group disappeared")
        return {"ok": True, "chat_id": chat_id, "enabled": new_value}

    @app.post("/api/telegram/groups/{chat_id}/backfill")
    async def backfill_one_group_route(
        chat_id: int, request: Request, _: None = Depends(auth.verify),
    ):
        """Subprocess out to `fetcher backfill-one` for this chat.
        Optional body {since_days: int} caps how far back to pull;
        omit for full history. Blocks up to 15 minutes — most chats
        finish in under a minute, but a years-old supergroup with
        thousands of messages can take several. Returns {seen, new}
        parsed from the subprocess's last log line."""
        pool = request.app.state.pool
        # Refuse if the group isn't enabled — there's no point pulling
        # history we won't keep ingesting.
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT enabled, title FROM raw.telegram_group_allowlist WHERE chat_id = $1",
                chat_id,
            )
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="group not in allowlist")
        if not row["enabled"]:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="group must be enabled before backfill (toggle on, then backfill)",
            )

        try:
            body = await request.json()
        except Exception:
            body = {}
        since_days = body.get("since_days") if isinstance(body, dict) else None

        env = os.environ.copy()
        env["BACKFILL_ONE_CHAT_ID"] = str(chat_id)
        if since_days is not None:
            env["BACKFILL_ONE_SINCE_DAYS"] = str(int(since_days))

        cmd = [
            "/srv/memory/apps/telethon/.venv/bin/python",
            "-m", "fetcher", "backfill-one",
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd="/srv/memory/apps/telethon",
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=900)
        except asyncio.TimeoutError:
            proc.kill()
            raise HTTPException(
                status.HTTP_504_GATEWAY_TIMEOUT,
                detail="backfill-one timed out after 15 min — large group? Try setting since_days.",
            )
        if proc.returncode != 0:
            tail = (stderr or b"").decode("utf-8", errors="replace")[-500:]
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"backfill-one exit {proc.returncode}: {tail}",
            )

        # Parse the "backfill_one_result seen=X new=Y" line emitted by
        # cmd_backfill_one on stdout. Tolerate it being absent.
        seen = new = 0
        for line in (stdout or b"").decode("utf-8", errors="replace").splitlines():
            if line.startswith("backfill_one_result"):
                for token in line.split():
                    if token.startswith("seen="):
                        try: seen = int(token[5:])
                        except ValueError: pass
                    elif token.startswith("new="):
                        try: new = int(token[4:])
                        except ValueError: pass
        return {
            "ok": True,
            "chat_id": chat_id,
            "title": row["title"],
            "seen": seen,
            "new": new,
            "log_tail": (stderr or b"").decode("utf-8", errors="replace")[-500:],
        }

    @app.post("/api/telegram/groups/discover")
    async def discover_telegram_groups_route(
        request: Request, _: None = Depends(auth.verify),
    ):
        """Run fetcher discover-groups server-side so the user can refresh
        the allowlist from the UI. Returns updated total count."""
        pool = request.app.state.pool
        cmd = [
            "/srv/memory/apps/telethon/.venv/bin/python",
            "-m", "fetcher", "discover-groups",
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd="/srv/memory/apps/telethon",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
        except asyncio.TimeoutError:
            proc.kill()
            raise HTTPException(
                status.HTTP_504_GATEWAY_TIMEOUT,
                detail="discover-groups timed out after 5 min",
            )
        if proc.returncode != 0:
            tail = (stderr or b"").decode("utf-8", errors="replace")[-500:]
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"discover-groups exit {proc.returncode}: {tail}",
            )
        total = await queries.count_telegram_groups(
            pool, q=None, kind=None, enabled=None,
        )
        return {
            "ok": True,
            "total": total,
            "log_tail": (stderr or b"").decode("utf-8", errors="replace")[-500:],
        }

    @app.post("/api/merge/regenerate")
    async def regenerate_candidates_route(
        request: Request, _: None = Depends(auth.verify),
    ):
        """Run the enrichment generate-candidates job server-side so the
        user can refresh the merge queue from the UI instead of SSH-ing
        to the droplet. Blocks up to 5 minutes (the corpus-wide run
        usually finishes in well under a minute). After the generator
        completes, sweep the three auto-reject filters (zombie /
        incompatible / weak-fuzzy-shape) so any new noise is collapsed
        before the next page render."""
        pool = request.app.state.pool
        cmd = [
            "/srv/memory/apps/enrichment/.venv/bin/python",
            "-m", "enrichment", "generate-candidates",
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd="/srv/memory/apps/enrichment",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=300,
            )
        except asyncio.TimeoutError:
            proc.kill()
            raise HTTPException(
                status.HTTP_504_GATEWAY_TIMEOUT,
                detail="generate-candidates timed out after 5 min",
            )
        if proc.returncode != 0:
            tail = (stderr or b"").decode("utf-8", errors="replace")[-500:]
            log.error("generate-candidates failed (rc=%d): %s", proc.returncode, tail)
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"generate-candidates exit {proc.returncode}: {tail}",
            )

        # Apply the same sweeps the lifespan does on startup so any new
        # noise the generator emitted is collapsed before the UI refresh.
        nz = await queries.auto_reject_zombie_candidates(pool)
        ni = await queries.auto_reject_incompatible_candidates(pool)
        nw = await queries.auto_reject_weak_fuzzy_name_candidates(pool)

        # Live-pending count after sweeps — matches the queue's filter.
        async with pool.acquire() as conn:
            live = await conn.fetchval(
                """
                SELECT count(*)
                FROM memory.merge_candidate mc
                JOIN canonical.person lp ON lp.id=mc.left_person_id
                 AND lp.merged_into IS NULL AND lp.deleted_at IS NULL
                JOIN canonical.person rp ON rp.id=mc.right_person_id
                 AND rp.merged_into IS NULL AND rp.deleted_at IS NULL
                WHERE mc.status='pending'
                """
            )
        return {
            "ok": True,
            "live_pending": int(live or 0),
            "auto_rejected": {"zombie": nz, "incompatible": ni, "weak_fuzzy": nw},
            "log_tail": (stderr or b"").decode("utf-8", errors="replace")[-800:],
        }

    @app.get("/api/persons/{person_id}/pending-merges")
    async def list_pending_for_person_route(
        person_id: str, request: Request, _: None = Depends(auth.verify),
    ):
        """Pending merge candidates involving this person. Used by the
        person-detail card to surface 'this person has N possible matches
        waiting for review' with a one-click jump to the merge queue."""
        pool = request.app.state.pool
        return await queries.list_pending_for_person(pool, person_id)

    @app.get("/api/merge/candidates")
    async def list_candidates_route(
        request: Request, limit: int = 20, offset: int = 0,
        _: None = Depends(auth.verify),
    ):
        pool = request.app.state.pool
        limit = max(1, min(limit, 100))
        cands = await queries.list_candidates(pool, limit=limit, offset=offset)
        # Hydrate full PersonDetail for both sides
        out: list[dict] = []
        for c in cands:
            left = await queries.get_person(pool, c["left_id"])
            right = await queries.get_person(pool, c["right_id"])
            if left is None or right is None:
                continue  # orphan candidate (person was merged/deleted)
            out.append({
                "id": c["id"], "source": c["source"], "confidence": c["confidence"],
                "score": c["score"], "evidence": c["evidence"],
                "created_at": c["created_at"], "left": left, "right": right,
            })
        return out

    @app.get("/api/merge/candidates/{candidate_id}")
    async def get_candidate_route(
        candidate_id: int, request: Request, _: None = Depends(auth.verify),
    ):
        """Hydrated single-candidate fetch (with both PersonDetail sides),
        used by the merge UI when the user lands via ?focus=<id>."""
        pool = request.app.state.pool
        c = await queries.get_candidate(pool, candidate_id)
        if c is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="candidate not found")
        left = await queries.get_person(pool, c["left_id"])
        right = await queries.get_person(pool, c["right_id"])
        if left is None or right is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="orphan candidate")
        return {
            "id": c["id"], "source": c["source"], "confidence": c["confidence"],
            "score": c["score"], "evidence": c["evidence"],
            "created_at": c["created_at"], "left": left, "right": right,
        }

    @app.get("/api/merge/candidates/{candidate_id}/related")
    async def list_related_route(
        candidate_id: int, request: Request, limit: int = 8,
        _: None = Depends(auth.verify),
    ):
        """Other pending candidates that share either person with this pair.
        Used by the merge UI to cluster a person's multiple match suggestions."""
        pool = request.app.state.pool
        c = await queries.get_candidate(pool, candidate_id)
        if c is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="candidate not found")
        related = await queries.list_related_candidates(
            pool, candidate_id=candidate_id,
            left_id=c["left_id"], right_id=c["right_id"],
            limit=max(1, min(limit, 30)),
        )
        out: list[dict] = []
        for r in related:
            left = await queries.get_person(pool, r["left_id"])
            right = await queries.get_person(pool, r["right_id"])
            if left is None or right is None:
                continue
            out.append({
                "id": r["id"], "source": r["source"], "confidence": r["confidence"],
                "score": r["score"], "evidence": r["evidence"],
                "created_at": r["created_at"], "left": left, "right": right,
            })
        return out

    @app.post("/api/merge/candidates/{candidate_id}/decision")
    async def decide_candidate_route(
        candidate_id: int, body: MergeDecisionIn, request: Request,
        _: None = Depends(auth.verify),
    ):
        pool = request.app.state.pool
        c = await queries.get_candidate(pool, candidate_id)
        if c is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="candidate not found")
        if c["status"] != "pending":
            raise HTTPException(status.HTTP_409_CONFLICT, detail=f"already {c['status']}")

        winner_id = loser_id = None
        if body.decision == "approve":
            if body.winner not in ("left", "right"):
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    detail="approve requires 'winner' = 'left' or 'right'",
                )
            winner_id = c["left_id"] if body.winner == "left" else c["right_id"]
            loser_id = c["right_id"] if body.winner == "left" else c["left_id"]
            note = body.note or f"merged via UI (candidate #{candidate_id}, source={c['source']})"
            await queries.execute_merge(pool, winner_id, loser_id, note)

        await queries.decide_candidate(
            pool, candidate_id,
            decision=body.decision,
            winner_person_id=winner_id, loser_person_id=loser_id,
            note=body.note,
        )
        return {"ok": True, "decision": body.decision, "winner_id": winner_id, "loser_id": loser_id}

    # ================================================================
    # Finance / budget module  (/api/finance/*)
    # ================================================================

    # --- assets ---
    @app.get("/api/finance/assets", response_model=list[FinAssetRow])
    async def fin_list_assets(request: Request, active_only: bool = False, _: None = Depends(auth.verify)):
        return await queries.list_fin_assets(request.app.state.pool, active_only=active_only)

    @app.post("/api/finance/assets", response_model=FinAssetRow)
    async def fin_create_asset(body: FinAssetCreate, request: Request, _: None = Depends(auth.verify)):
        return await queries.create_fin_asset(request.app.state.pool, body.model_dump(exclude_unset=False))

    @app.patch("/api/finance/assets/{asset_id}", response_model=FinAssetRow)
    async def fin_patch_asset(asset_id: str, body: FinAssetPatch, request: Request, _: None = Depends(auth.verify)):
        row = await queries.patch_fin_asset(request.app.state.pool, asset_id, body.model_dump(exclude_unset=True))
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="asset not found")
        return row

    # --- accounts ---
    @app.get("/api/finance/accounts", response_model=list[FinAccountRow])
    async def fin_list_accounts(request: Request, include_archived: bool = False,
                                account_class: str | None = None, _: None = Depends(auth.verify)):
        viewer = await auth.resolve_viewer(request)
        return await queries.list_fin_accounts(
            request.app.state.pool, include_archived=include_archived,
            account_class=account_class, viewer=viewer)

    @app.get("/api/finance/crypto-cutoff")
    async def fin_get_cutoff(request: Request, _: None = Depends(auth.verify)):
        v = await queries.get_setting(request.app.state.pool, "crypto_tx_cutoff", "2026-06-01")
        return {"cutoff": v}

    @app.put("/api/finance/crypto-cutoff")
    async def fin_set_cutoff(request: Request, body: dict, _: None = Depends(auth.verify)):
        from datetime import date as _date
        c = (body or {}).get("cutoff")
        try:
            _date.fromisoformat(str(c))
        except (ValueError, TypeError):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="cutoff must be YYYY-MM-DD")
        await queries.set_setting(request.app.state.pool, "crypto_tx_cutoff", c)
        return {"cutoff": c}

    @app.post("/api/finance/accounts", response_model=FinAccountRow)
    async def fin_create_account(body: FinAccountCreate, request: Request, _: None = Depends(auth.verify)):
        viewer = await auth.resolve_viewer(request)
        fields = body.model_dump(exclude_unset=False)
        # a member creating an account owns it by default (so 'private' is theirs)
        if viewer and viewer.get("member_id") and not fields.get("owner_member_id"):
            fields["owner_member_id"] = viewer["member_id"]
        return await queries.create_fin_account(request.app.state.pool, fields)

    @app.get("/api/finance/accounts/{account_id}", response_model=FinAccountRow)
    async def fin_get_account(account_id: str, request: Request, _: None = Depends(auth.verify)):
        viewer = await auth.resolve_viewer(request)
        row = await queries.get_fin_account(request.app.state.pool, account_id, viewer=viewer)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="account not found")
        return row

    @app.patch("/api/finance/accounts/{account_id}", response_model=FinAccountRow)
    async def fin_patch_account(account_id: str, body: FinAccountPatch, request: Request, _: None = Depends(auth.verify)):
        pool = request.app.state.pool
        viewer = await auth.resolve_viewer(request)
        if viewer:   # a scoped member
            cur = await queries.get_fin_account(pool, account_id, viewer=viewer)
            if cur is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, detail="account not found")
            fields = body.model_dump(exclude_unset=True)
            # only the account's owner may change its sharing (visibility/owner)
            changes_sharing = "visibility" in fields or "owner_member_id" in fields
            if changes_sharing and cur.get("owner_member_id") != viewer.get("member_id"):
                raise HTTPException(status.HTTP_403_FORBIDDEN,
                                    detail="only the account owner can change sharing")
        row = await queries.patch_fin_account(pool, account_id, body.model_dump(exclude_unset=True))
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="account not found")
        return row

    @app.delete("/api/finance/accounts/{account_id}", status_code=204)
    async def fin_delete_account(account_id: str, request: Request, _: None = Depends(auth.verify)):
        pool = request.app.state.pool
        viewer = await auth.resolve_viewer(request)
        if viewer:   # a member may delete only an account they own
            cur = await queries.get_fin_account(pool, account_id, viewer=viewer)
            if cur is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, detail="account not found")
            if cur.get("owner_member_id") != viewer.get("member_id"):
                raise HTTPException(status.HTTP_403_FORBIDDEN, detail="not your account")
        if not await queries.soft_delete_fin_account(pool, account_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="account not found")

    # --- members (sharing PR1; app-owner / admin only) ---
    @app.get("/api/finance/members", response_model=list[FinMemberRow])
    async def fin_list_members(request: Request, role: str = Depends(auth.verify)):
        if role != "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="owner only")
        return await queries.list_fin_members(request.app.state.pool)

    @app.post("/api/finance/members", response_model=FinMemberRow)
    async def fin_create_member(body: FinMemberCreate, request: Request, role: str = Depends(auth.verify)):
        if role != "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="owner only")
        try:
            return await queries.create_fin_member(request.app.state.pool, body.model_dump(exclude_unset=False))
        except Exception as e:  # noqa: BLE001 — unique (email/actor) clash → 400
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"member exists or invalid: {str(e)[:120]}")

    @app.patch("/api/finance/members/{member_id}", response_model=FinMemberRow)
    async def fin_patch_member(member_id: str, body: FinMemberPatch, request: Request, role: str = Depends(auth.verify)):
        if role != "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="owner only")
        row = await queries.patch_fin_member(request.app.state.pool, member_id, body.model_dump(exclude_unset=True))
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="member not found")
        return row

    # --- categories ---
    @app.get("/api/finance/categories", response_model=list[FinCategoryRow])
    async def fin_list_categories(request: Request, _: None = Depends(auth.verify)):
        return await queries.list_fin_categories(request.app.state.pool)

    @app.post("/api/finance/categories", response_model=FinCategoryRow)
    async def fin_create_category(body: FinCategoryCreate, request: Request, _: None = Depends(auth.verify)):
        return await queries.create_fin_category(request.app.state.pool, body.model_dump(exclude_unset=False))

    @app.patch("/api/finance/categories/{key}", response_model=FinCategoryRow)
    async def fin_patch_category(key: str, body: FinCategoryPatch, request: Request, _: None = Depends(auth.verify)):
        row = await queries.patch_fin_category(request.app.state.pool, key, body.model_dump(exclude_unset=True))
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="category not found")
        return row

    @app.delete("/api/finance/categories/{key}", status_code=204)
    async def fin_delete_category(key: str, request: Request, _: None = Depends(auth.verify)):
        err = await queries.delete_fin_category(request.app.state.pool, key)
        if err == "not_found":
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="category not found")
        if err == "in_use":
            raise HTTPException(status.HTTP_409_CONFLICT, detail="category has transactions — reassign them first")

    # --- payees ---
    @app.get("/api/finance/payees", response_model=list[FinPayeeRow])
    async def fin_list_payees(request: Request, q: str | None = None, _: None = Depends(auth.verify)):
        return await queries.list_fin_payees(request.app.state.pool, q=q)

    @app.post("/api/finance/payees", response_model=FinPayeeRow)
    async def fin_create_payee(body: FinPayeeCreate, request: Request, _: None = Depends(auth.verify)):
        return await queries.create_fin_payee(request.app.state.pool, body.model_dump(exclude_unset=False))

    @app.patch("/api/finance/payees/{payee_id}", response_model=FinPayeeRow)
    async def fin_patch_payee(payee_id: str, body: FinPayeePatch, request: Request, _: None = Depends(auth.verify)):
        row = await queries.patch_fin_payee(request.app.state.pool, payee_id, body.model_dump(exclude_unset=True))
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="payee not found")
        return row

    # --- budgets ---
    @app.get("/api/finance/budgets", response_model=list[FinBudgetRow])
    async def fin_list_budgets(request: Request, month: str | None = None, _: None = Depends(auth.verify)):
        from datetime import date as _date
        if month:
            ms = _date.fromisoformat(month if len(month) > 7 else month + "-01")
        else:
            t = _date.today()
            ms = t.replace(day=1)
        return await queries.list_fin_budgets(request.app.state.pool, month_start=ms)

    @app.put("/api/finance/budgets", response_model=FinBudgetRow)
    async def fin_upsert_budget(body: FinBudgetUpsert, request: Request, _: None = Depends(auth.verify)):
        await queries.upsert_fin_budget(request.app.state.pool, category_key=body.category_key, limit_usd=body.limit_usd)
        from datetime import date as _date
        rows = await queries.list_fin_budgets(request.app.state.pool, month_start=_date.today().replace(day=1))
        match = next((r for r in rows if r["category_key"] == body.category_key), None)
        if match is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="unknown category")
        return match

    @app.delete("/api/finance/budgets/{category_key}", status_code=204)
    async def fin_delete_budget(category_key: str, request: Request, _: None = Depends(auth.verify)):
        if not await queries.delete_fin_budget(request.app.state.pool, category_key):
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="budget not found")

    # --- holdings (portfolio: crypto + brokerage positions) ---
    @app.get("/api/finance/holdings", response_model=list[FinHoldingRow])
    async def fin_list_holdings(request: Request, account_id: str | None = None, _: None = Depends(auth.verify)):
        viewer = await auth.resolve_viewer(request)
        return await queries.list_fin_holdings(request.app.state.pool, account_id=account_id, viewer=viewer)

    @app.put("/api/finance/holdings", response_model=FinHoldingRow)
    async def fin_upsert_holding(body: FinHoldingUpsert, request: Request, _: None = Depends(auth.verify)):
        pool = request.app.state.pool
        asset_id = body.asset_id
        if not asset_id and body.asset_code:
            asset_id = await queries.get_or_create_asset_by_code(
                pool, body.asset_code, kind=body.asset_kind,
                decimals=8 if body.asset_kind == "crypto" else 2)
        if not asset_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="asset_id or asset_code required")
        row = await queries.upsert_fin_holding(
            pool, account_id=body.account_id, asset_id=asset_id,
            quantity=body.quantity, cost_basis_usd=body.cost_basis_usd, source="manual")
        if row is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="could not save holding")
        return row

    @app.delete("/api/finance/holdings/{holding_id}", status_code=204)
    async def fin_delete_holding(holding_id: str, request: Request, _: None = Depends(auth.verify)):
        if not await queries.delete_fin_holding(request.app.state.pool, holding_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="holding not found")

    # --- investments: FIFO cost lots (Phase 2) ---
    @app.get("/api/finance/lots", response_model=list[FinLotRow])
    async def fin_list_lots(request: Request, account_id: str | None = None,
                            asset_id: str | None = None, _: None = Depends(auth.verify)):
        return await queries.list_fin_lots(request.app.state.pool,
                                           account_id=account_id, asset_id=asset_id)

    @app.post("/api/finance/lots", response_model=FinLotRow)
    async def fin_create_lot(body: FinLotCreate, request: Request, _: None = Depends(auth.verify)):
        pool = request.app.state.pool
        asset_id = body.asset_id
        if not asset_id and body.asset_code:
            asset_id = await queries.get_or_create_asset_by_code(
                pool, body.asset_code, kind=body.asset_kind,
                decimals=8 if body.asset_kind == "crypto" else 2)
        if not asset_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="asset_id or asset_code required")
        row = await queries.insert_fin_lot(
            pool, account_id=body.account_id, asset_id=asset_id, open_date=body.open_date,
            quantity=body.quantity, cost_per_unit_usd=body.cost_per_unit_usd, note=body.note)
        if row is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="could not save lot")
        return row

    @app.delete("/api/finance/lots/{lot_id}", status_code=204)
    async def fin_delete_lot(lot_id: str, request: Request, _: None = Depends(auth.verify)):
        if not await queries.delete_fin_lot(request.app.state.pool, lot_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="lot not found")

    @app.get("/api/finance/sales", response_model=list[FinSaleRow])
    async def fin_list_sales(request: Request, account_id: str | None = None,
                             asset_id: str | None = None, _: None = Depends(auth.verify)):
        return await queries.list_fin_sales(request.app.state.pool,
                                            account_id=account_id, asset_id=asset_id)

    @app.post("/api/finance/sales", response_model=FinSaleRow)
    async def fin_create_sale(body: FinSaleCreate, request: Request, _: None = Depends(auth.verify)):
        pool = request.app.state.pool
        # Reject an oversell — can't sell more than the FIFO-remaining open quantity.
        remaining = await queries.fin_remaining_quantity(
            pool, account_id=body.account_id, asset_id=body.asset_id)
        from decimal import Decimal
        if Decimal(str(body.quantity)) > remaining:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=f"cannot sell {body.quantity}: only {remaining} open in this position")
        row = await queries.insert_fin_sale(
            pool, account_id=body.account_id, asset_id=body.asset_id, sale_date=body.sale_date,
            quantity=body.quantity, proceeds_per_unit_usd=body.proceeds_per_unit_usd, note=body.note)
        if row is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="could not save sale")
        return row

    @app.delete("/api/finance/sales/{sale_id}", status_code=204)
    async def fin_delete_sale(sale_id: str, request: Request, _: None = Depends(auth.verify)):
        if not await queries.delete_fin_sale(request.app.state.pool, sale_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="sale not found")

    @app.get("/api/finance/positions", response_model=list[FinPositionRow])
    async def fin_positions(request: Request, account_id: str, _: None = Depends(auth.verify)):
        pool = request.app.state.pool
        if not queries.is_uuid(account_id):   # empty/malformed → 400, not a 500 uuid cast
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="invalid account_id")
        viewer = await auth.resolve_viewer(request)
        if not await queries.member_can_see_account(pool, viewer, account_id):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="not your account")
        return await queries.compute_fin_positions(pool, account_id=account_id)

    @app.post("/api/finance/sync-wallets")
    async def fin_sync_wallets(request: Request, _: None = Depends(auth.verify)):
        """Read on-chain balances for every wallet-linked account → holdings,
        plus the in/out transfer feed (with gas + block-time USD) for operational
        crypto wallets. Each wallet's outcome is stamped to fin_wallet_sync."""
        from . import crypto_sync
        return await crypto_sync.sync_all_wallets(
            request.app.state.pool, request.app.state.cfg)

    @app.get("/api/finance/wallets/summary")
    async def fin_wallets_summary(request: Request, _: None = Depends(auth.verify)):
        """Multi-wallet aggregation: holdings rolled up per asset across every
        on-chain wallet, a per-wallet breakdown with gas spend + transfer counts,
        and each wallet's latest sync health."""
        viewer = await auth.resolve_viewer(request)
        return await queries.wallets_summary(request.app.state.pool, viewer=viewer)

    # --- planned / recurring transactions ---
    @app.get("/api/finance/planned", response_model=list[FinPlannedRow])
    async def fin_list_planned(request: Request, _: None = Depends(auth.verify)):
        return await queries.list_fin_planned(request.app.state.pool)

    @app.post("/api/finance/planned", response_model=FinPlannedRow)
    async def fin_create_planned(body: FinPlannedCreate, request: Request, _: None = Depends(auth.verify)):
        return await queries.create_fin_planned(request.app.state.pool, body.model_dump(exclude_unset=False))

    @app.patch("/api/finance/planned/{planned_id}", response_model=FinPlannedRow)
    async def fin_patch_planned(planned_id: str, body: FinPlannedPatch, request: Request, _: None = Depends(auth.verify)):
        row = await queries.patch_fin_planned(request.app.state.pool, planned_id, body.model_dump(exclude_unset=True))
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="planned transaction not found")
        return row

    @app.delete("/api/finance/planned/{planned_id}", status_code=204)
    async def fin_delete_planned(planned_id: str, request: Request, _: None = Depends(auth.verify)):
        if not await queries.soft_delete_fin_planned(request.app.state.pool, planned_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="planned transaction not found")

    @app.post("/api/finance/planned/{planned_id}/post", response_model=FinPlannedRow)
    async def fin_post_planned(planned_id: str, request: Request, _: None = Depends(auth.verify)):
        """Post the current occurrence now (manual / reminder) and advance the schedule."""
        row = await queries.post_planned_now(request.app.state.pool, planned_id)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="planned transaction not found")
        return row

    # --- net worth + reports (literal paths BEFORE /{txn_id}) ---
    @app.get("/api/finance/net-worth", response_model=NetWorthRow)
    async def fin_net_worth(request: Request, _: None = Depends(auth.verify)):
        viewer = await auth.resolve_viewer(request)
        return await queries.net_worth(request.app.state.pool, viewer=viewer)

    @app.get("/api/finance/reports/spending")
    async def fin_report_spending(request: Request, date_from: str, date_to: str, _: None = Depends(auth.verify)):
        from datetime import date as _date
        viewer = await auth.resolve_viewer(request)
        return await queries.report_spending_by_category(
            request.app.state.pool, date_from=_date.fromisoformat(date_from),
            date_to=_date.fromisoformat(date_to), viewer=viewer)

    @app.get("/api/finance/reports/cashflow")
    async def fin_report_cashflow(request: Request, months: int = 6, _: None = Depends(auth.verify)):
        viewer = await auth.resolve_viewer(request)
        return await queries.report_cashflow(request.app.state.pool, months=max(1, min(months, 36)), viewer=viewer)

    # --- transactions ---
    @app.get("/api/finance/transactions", response_model=list[FinTransactionRow])
    async def fin_list_txns(
        request: Request,
        account_id: str | None = None, category_key: str | None = None,
        txn_type: str | None = None, date_from: str | None = None, date_to: str | None = None,
        q: str | None = None, limit: int = 100, offset: int = 0,
        _: None = Depends(auth.verify),
    ):
        from datetime import date as _date
        viewer = await auth.resolve_viewer(request)
        return await queries.list_fin_transactions(
            request.app.state.pool, account_id=account_id, category_key=category_key,
            txn_type=txn_type,
            date_from=_date.fromisoformat(date_from) if date_from else None,
            date_to=_date.fromisoformat(date_to) if date_to else None,
            q=q, limit=max(1, min(limit, 500)), offset=offset, viewer=viewer)

    @app.post("/api/finance/transactions", response_model=FinTransactionRow)
    async def fin_create_txn(body: FinTransactionCreate, request: Request, _: None = Depends(auth.verify)):
        pool = request.app.state.pool
        viewer = await auth.resolve_viewer(request)
        # a member may only book a transaction on accounts they can see
        if viewer and not await _txn_legs_visible(pool, viewer, body.model_dump()):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="not your account")
        return await queries.create_fin_transaction(pool, body.model_dump(exclude_unset=False))

    @app.get("/api/finance/transactions/{txn_id}", response_model=FinTransactionRow)
    async def fin_get_txn(txn_id: str, request: Request, _: None = Depends(auth.verify)):
        pool = request.app.state.pool
        row = await queries.get_fin_transaction(pool, txn_id)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="transaction not found")
        viewer = await auth.resolve_viewer(request)
        if viewer and not await _txn_legs_visible(pool, viewer, row):
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="transaction not found")
        return row

    @app.patch("/api/finance/transactions/{txn_id}", response_model=FinTransactionRow)
    async def fin_patch_txn(txn_id: str, body: FinTransactionPatch, request: Request, _: None = Depends(auth.verify)):
        pool = request.app.state.pool
        cur = await queries.get_fin_transaction(pool, txn_id)
        if cur is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="transaction not found")
        patch = body.model_dump(exclude_unset=True)
        # On-chain rows (source_kind='chain_tx'): the wallet leg + date are facts of
        # the chain, not editable — only the classification/counter-leg is. Strip any
        # attempt to change the locked side so the lock holds server-side too.
        locked = await queries.chain_txn_locked_side(pool, cur)
        if locked:
            for f in ("txn_date", f"{locked}_account_id", f"{locked}_asset_id",
                      f"{locked}_asset_code", f"{locked}_amount"):
                patch.pop(f, None)
        viewer = await auth.resolve_viewer(request)
        if viewer:
            if not await _txn_legs_visible(pool, viewer, cur):
                raise HTTPException(status.HTTP_404_NOT_FOUND, detail="transaction not found")
            # editing a txn on an account the member doesn't OWN → queue for owner
            # approval (they edit their own accounts directly, shared or not)
            if not await queries.txn_owned_by_member(pool, viewer.get("member_id"), cur):
                if not viewer.get("member_id"):
                    raise HTTPException(status.HTTP_403_FORBIDDEN, detail="needs owner approval (no member identity)")
                ap = await queries.create_approval(
                    pool, requested_by=viewer["member_id"], action="update_txn",
                    target_id=txn_id, payload=patch)
                return JSONResponse(status_code=202, content={"status": "pending_approval", "approval_id": ap["id"]})
        row = await queries.patch_fin_transaction(pool, txn_id, patch)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="transaction not found")
        return row

    @app.delete("/api/finance/transactions/{txn_id}", status_code=204)
    async def fin_delete_txn(txn_id: str, request: Request, _: None = Depends(auth.verify)):
        pool = request.app.state.pool
        viewer = await auth.resolve_viewer(request)
        if viewer:
            cur = await queries.get_fin_transaction(pool, txn_id)
            if cur is None or not await _txn_legs_visible(pool, viewer, cur):
                raise HTTPException(status.HTTP_404_NOT_FOUND, detail="transaction not found")
            # deleting a txn on an account the member doesn't OWN → queue for approval
            if not await queries.txn_owned_by_member(pool, viewer.get("member_id"), cur):
                if not viewer.get("member_id"):
                    raise HTTPException(status.HTTP_403_FORBIDDEN, detail="needs owner approval (no member identity)")
                ap = await queries.create_approval(
                    pool, requested_by=viewer["member_id"], action="delete_txn",
                    target_id=txn_id, payload={})
                return JSONResponse(status_code=202, content={"status": "pending_approval", "approval_id": ap["id"]})
        if not await queries.soft_delete_fin_transaction(pool, txn_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="transaction not found")

    # --- approvals (sharing PR3) ---
    @app.get("/api/finance/approvals", response_model=list[FinApprovalRow])
    async def fin_list_approvals(request: Request, status_filter: str | None = None, role: str = Depends(auth.verify)):
        pool = request.app.state.pool
        if role == "admin":
            return await queries.list_approvals(pool, status=status_filter)   # owner sees all
        viewer = await auth.resolve_viewer(request)                            # member sees only their own
        mid = viewer.get("member_id") if viewer else None
        if not mid:
            return []
        return await queries.list_approvals(pool, status=status_filter, requester_member_id=mid)

    @app.get("/api/finance/approvals/pending-count")
    async def fin_pending_approvals_count(request: Request, role: str = Depends(auth.verify)):
        if role != "admin":
            return {"count": 0}
        return {"count": await queries.count_pending_approvals(request.app.state.pool)}

    @app.post("/api/finance/approvals/{approval_id}/approve", response_model=FinApprovalRow)
    async def fin_approve(approval_id: str, request: Request,
                          body: FinApprovalDecision | None = None, role: str = Depends(auth.verify)):
        if role != "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="owner only")
        pool = request.app.state.pool
        decided_by = await _acting_member_id(request, pool)
        row = await queries.decide_approval(pool, approval_id, decided_by=decided_by,
                                            approve=True, note=(body.note if body else None))
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="approval not found")
        return row

    @app.post("/api/finance/approvals/{approval_id}/reject", response_model=FinApprovalRow)
    async def fin_reject(approval_id: str, request: Request,
                         body: FinApprovalDecision | None = None, role: str = Depends(auth.verify)):
        if role != "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="owner only")
        pool = request.app.state.pool
        decided_by = await _acting_member_id(request, pool)
        row = await queries.decide_approval(pool, approval_id, decided_by=decided_by,
                                            approve=False, note=(body.note if body else None))
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="approval not found")
        return row

    # --- imports (ZenMoney + PDF/CSV) ---
    @app.get("/api/finance/imports", response_model=list[FinImportBatchRow])
    async def fin_list_imports(request: Request, status_filter: str | None = None, _: None = Depends(auth.verify)):
        return await queries.list_fin_import_batches(request.app.state.pool, status=status_filter)

    @app.post("/api/finance/import/zenmoney")
    async def fin_import_zenmoney(request: Request, _: None = Depends(auth.verify)):
        from . import finance_import
        cfg = request.app.state.cfg
        if not getattr(cfg, "zenmoney_token", None):
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="ZENMONEY_TOKEN not configured")
        try:
            summary = await finance_import.sync_zenmoney(request.app.state.pool, cfg.zenmoney_token)
        except finance_import.ZenMoneyError as e:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(e))
        return {"ok": True, **summary}

    @app.post("/api/finance/import/pdf", response_model=FinImportBatchRow)
    async def fin_import_pdf(
        request: Request,
        file: UploadFile = File(...),
        account_id: str | None = Form(None),
        _: None = Depends(auth.verify),
    ):
        from . import finance_import
        client = request.app.state.anthropic
        if client is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="ANTHROPIC_API_KEY not configured")
        raw = await file.read()
        try:
            parsed = await finance_import.parse_statement(
                client, request.app.state.cfg.statement_model,
                filename=file.filename or "statement", content=raw)
        except finance_import.ImportParseError as e:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
        return await queries.create_fin_import_batch(
            request.app.state.pool, kind=parsed["kind"], filename=file.filename,
            account_id=account_id, parsed=parsed)

    @app.post("/api/finance/imports/{batch_id}/confirm")
    async def fin_confirm_import(batch_id: str, body: ImportConfirmIn, request: Request, _: None = Depends(auth.verify)):
        from . import finance_import
        pool = request.app.state.pool
        batch = await queries.get_fin_import_batch(pool, batch_id)
        if batch is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="import batch not found")
        if batch["status"] != "pending":
            raise HTTPException(status.HTTP_409_CONFLICT, detail=f"already {batch['status']}")
        account_id = body.account_id or batch.get("account_id")
        if not account_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="pick an account to import into")
        res = await finance_import.commit_statement_batch(
            pool, batch, account_id=account_id, skip_indices=set(body.skip_indices),
            client=request.app.state.anthropic, model=request.app.state.cfg.extraction_model)
        await queries.mark_import_batch(pool, batch_id, "confirmed")
        return {"ok": True, **res}

    @app.post("/api/finance/categorize")
    async def fin_categorize(request: Request, dry_run: bool = True, _: None = Depends(auth.verify)):
        """Learn from categorized history and apply high-confidence category
        matches to the uncategorized backlog (Phase 1). dry_run=true (default)
        reports what it would do and writes nothing; pass dry_run=false to apply
        (applied rows are tagged 'auto:history' so they're reversible)."""
        from . import finance_categorize
        return await finance_categorize.run_categorize(request.app.state.pool, dry_run=dry_run)

    @app.post("/api/finance/enrich-llm")
    async def fin_enrich_llm(request: Request, apply: bool = False, limit: int = 400,
                             cloud: bool = True, _: None = Depends(auth.verify)):
        """Enrich each distinct enrichable memo (not self/generic/crypto) with a
        category + clean payee. cloud=true uses the strong Haiku extractor
        (default — the local 3b model tested too weak); cloud=false uses local
        Ollama. apply=false previews (no writes); apply=true writes, tagging rows
        'auto:cat' (cloud) / 'auto:llm' (local) for review."""
        from . import finance_categorize
        if cloud:
            client = request.app.state.anthropic
            if client is None:
                raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="ANTHROPIC_API_KEY not configured")
            return await finance_categorize.enrich_backlog_cloud(
                request.app.state.pool, client, request.app.state.cfg.extraction_model,
                apply=apply, limit=limit)
        return await finance_categorize.enrich_backlog_with_ollama(
            request.app.state.pool, request.app.state.cfg, apply=apply, limit=limit)

    @app.post("/api/finance/imports/{batch_id}/discard")
    async def fin_discard_import(batch_id: str, request: Request, _: None = Depends(auth.verify)):
        ok = await queries.mark_import_batch(request.app.state.pool, batch_id, "discarded")
        if not ok:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="not a pending batch")
        return {"ok": True}

    return app


app = create_app()
