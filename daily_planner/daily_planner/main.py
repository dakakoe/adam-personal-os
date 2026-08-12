from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import asyncpg

from . import prompt, queries
from .config import Config

log = logging.getLogger(__name__)


def _resolve_plan_date(cfg: Config):
    """Which calendar day this plan is for, in the configured tz.
      tomorrow → next day (the nightly timer's mode)
      today    → today (the UI regenerate mode)
      auto     → tomorrow once it's evening (>= evening_hour), else today
    """
    now_local = datetime.now(ZoneInfo(cfg.timezone))
    today = now_local.date()
    if cfg.target == "today":
        return today
    if cfg.target == "tomorrow":
        return today + timedelta(days=1)
    # auto
    return today + timedelta(days=1) if now_local.hour >= cfg.evening_hour else today


def _fallback_narrative(data: dict) -> str:
    """No-LLM (or LLM-failed) plan: a deterministic, honest summary so the
    /today view is never empty."""
    c = data["counts"]
    if not any(c.values()):
        return "Clear board — nothing open, no deals in flight, inbox empty. Use the day to get ahead."
    parts = []
    if c.get("events"):
        parts.append(f"{c['events']} meeting(s)")
    if c["open_tasks"]:
        parts.append(f"{c['open_tasks']} open task(s)")
    if c["live_opps"]:
        parts.append(f"{c['live_opps']} live deal(s)")
    if c["owes_reply"]:
        parts.append(f"{c['owes_reply']} reply(ies) owed")
    if c["pending_suggestions"]:
        parts.append(f"{c['pending_suggestions']} inbox item(s) to triage")
    return "On the board: " + ", ".join(parts) + "."


def _fallback_focus(data: dict) -> list[dict]:
    """Deterministic top picks when there's no LLM: overdue/soonest tasks,
    then highest-momentum deals, then oldest replies owed. Capped at 5."""
    focus: list[dict] = []
    for t in data["tasks"][:3]:
        due = f", due {t['due_date']}" if t.get("due_date") else ""
        focus.append({
            "title": t["title"],
            "reason": f"{t['status']} task{due}",
            "kind": "task",
            "ref_id": t["id"],
            "project_slug": t.get("project_slug"),
        })
    for o in data["opportunities"][:1]:
        focus.append({
            "title": o["title"],
            "reason": f"deal at {o['stage']} stage",
            "kind": "opportunity",
            "ref_id": o["id"],
            "project_slug": o.get("project_slug"),
        })
    for p in data["owes_reply"][:1]:
        focus.append({
            "title": f"Reply to {p['display_name']}",
            "reason": "messaged you, no response since",
            "kind": "reply",
            "ref_id": p["person_id"],
            "project_slug": None,
        })
    return focus[:5]


def _llm_plan(cfg: Config, payload: str) -> dict | None:
    """Forced-tool-use Haiku call. Returns {narrative, focus} or None on
    any failure (caller falls back). Sync client run off the loop."""
    import anthropic
    client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)
    resp = client.messages.create(
        model=cfg.model,
        max_tokens=1500,
        system=prompt.SYSTEM_PROMPT,
        tools=[prompt.SAVE_TOOL],
        tool_choice={"type": "tool", "name": "record_daily_plan"},
        messages=[{"role": "user", "content": payload}],
    )
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "record_daily_plan":
            return dict(block.input)
    return None


def run(cfg: Config) -> int:
    import asyncio
    return asyncio.run(_run_async(cfg))


async def _run_async(cfg: Config) -> int:
    plan_date = _resolve_plan_date(cfg)
    # Calendar window: the plan day plus the next, in the configured tz
    # (tz-aware so asyncpg compares against timestamptz correctly).
    tz = ZoneInfo(cfg.timezone)
    window_start = datetime.combine(plan_date, time.min, tzinfo=tz)
    window_end = window_start + timedelta(days=2)

    pool = await asyncpg.create_pool(
        cfg.db_url, min_size=1, max_size=2, statement_cache_size=0
    )
    try:
        async with pool.acquire() as conn:
            data = await queries.gather(
                conn, cfg, window_start=window_start, window_end=window_end
            )

        c = data["counts"]
        log.info(
            "planner: target=%s plan_date=%s tasks=%d opps=%d owes=%d pending=%d events=%d model=%s",
            cfg.target, plan_date, c["open_tasks"], c["live_opps"],
            c["owes_reply"], c["pending_suggestions"], c.get("events", 0), cfg.model,
        )

        narrative: str | None = None
        focus: list[dict] = []
        used_llm = False
        if cfg.anthropic_api_key:
            payload = prompt.render(
                plan_date=plan_date.isoformat(),
                self_label=cfg.self_label,
                data=data,
                timezone=cfg.timezone,
            )
            try:
                import asyncio
                result = await asyncio.to_thread(_llm_plan, cfg, payload)
            except Exception:
                log.exception("LLM plan failed; using deterministic fallback")
                result = None
            if result:
                narrative = (result.get("narrative") or "").strip() or None
                focus = result.get("focus") or []
                used_llm = True

        if not used_llm:
            narrative = _fallback_narrative(data)
            focus = _fallback_focus(data)

        structured = {
            "focus": focus,
            "counts": data["counts"],
            "tasks": data["tasks"],
            "opportunities": data["opportunities"],
            "owes_reply": data["owes_reply"],
            "suggestions": data["suggestions"],
            "recaps": data["recaps"],
            "events": data.get("events", []),
            "timezone": cfg.timezone,
            "model": cfg.model if used_llm else None,
            "generated_via": "llm" if used_llm else "fallback",
        }

        if cfg.dry_run:
            log.info("DRY RUN — not writing. narrative=%r focus=%d items", narrative, len(focus))
            print(narrative)
            for f in focus:
                print(f"  • [{f.get('kind')}] {f.get('title')} — {f.get('reason')}")
            return 0

        async with pool.acquire() as conn:
            plan_id = await queries.upsert_daily_plan(
                conn, plan_date=plan_date, narrative=narrative, structured=structured,
            )
        log.info("planner: wrote daily_plan %s for %s (%d focus items, via=%s)",
                 plan_id, plan_date, len(focus), structured["generated_via"])
        return 0
    finally:
        await pool.close()
