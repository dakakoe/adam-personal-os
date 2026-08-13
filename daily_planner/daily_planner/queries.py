"""Read-side queries that gather the planner's inputs. Everything here is
already on the droplet (Phase 1-3 tables + the canonical graph)."""

from __future__ import annotations

import json

import asyncpg


# Open / in-progress tasks, soonest-due first. Overdue floats to the top
# (due_date ASC, NULLs last). The model leans on due_date to prioritize.
OPEN_TASKS_SQL = """
SELECT t.id::text, t.title, t.status, t.due_date,
       p.slug AS project_slug, p.name AS project_name,
       wp.display_name AS with_person_name
  FROM memory.task t
  LEFT JOIN memory.project p ON p.id = t.project_id
  LEFT JOIN canonical.person wp ON wp.id = t.with_person_id
                                AND wp.merged_into IS NULL
                                AND wp.deleted_at IS NULL
 WHERE t.deleted_at IS NULL
   AND t.status IN ('open', 'doing')
 ORDER BY
   CASE t.status WHEN 'doing' THEN 0 ELSE 1 END,
   t.due_date ASC NULLS LAST,
   t.created_at DESC
 LIMIT $1
"""

# Live opportunities (not lost, not closed), momentum-ordered: the deals
# closest to the finish line first.
LIVE_OPPS_SQL = """
SELECT o.id::text, o.title, o.stage::text AS stage, o.estimated_value,
       o.updated_at,
       p.slug AS project_slug, p.name AS project_name,
       cp.display_name AS counterparty_name
  FROM memory.opportunity o
  LEFT JOIN memory.project p ON p.id = o.project_id
  LEFT JOIN canonical.person cp ON cp.id = o.counterparty_id
                                AND cp.merged_into IS NULL
                                AND cp.deleted_at IS NULL
 WHERE o.deleted_at IS NULL
   AND o.stage <> 'lost'
   AND o.closed_at IS NULL
 ORDER BY
   CASE o.stage::text
     WHEN 'contract' THEN 0 WHEN 'mou' THEN 1 WHEN 'conversations' THEN 2
     WHEN 'intro' THEN 3 WHEN 'active' THEN 4 ELSE 5 END,
   o.updated_at DESC
 LIMIT $1
"""

# Top pending suggestions (the inbox), high-confidence first.
TOP_SUGGESTIONS_SQL = """
SELECT s.id::text, s.kind, s.title, s.confidence, s.source_kind,
       cp.display_name AS person_name
  FROM memory.suggestion s
  LEFT JOIN canonical.person cp ON cp.id = s.person_id
 WHERE s.status = 'pending'
 ORDER BY
   CASE s.confidence WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
   s.created_at DESC
 LIMIT $1
"""

COUNT_PENDING_SUGGESTIONS_SQL = """
SELECT count(*) FROM memory.suggestion WHERE status = 'pending'
"""

# Recent meeting recaps (last N days), newest first — fresh context for
# what the day should follow up on.
RECENT_RECAPS_SQL = """
SELECT id::text, title, meeting_date, recap
  FROM memory.meeting_recap
 WHERE meeting_date IS NOT NULL
   AND meeting_date > now() - make_interval(days => $1)
 ORDER BY meeting_date DESC
 LIMIT $2
"""

# "Owes a reply": a contact messaged in the window and the user hasn't
# replied since (no outbound after their last inbound). Mirrors the
# interaction_scanner two-way logic, inverted.
OWES_REPLY_SQL = """
WITH recent AS (
  SELECT person_id,
         max(occurred_at) FILTER (WHERE direction = 'inbound')  AS last_in,
         max(occurred_at) FILTER (WHERE direction = 'outbound') AS last_out,
         count(*) FILTER (WHERE direction = 'inbound')          AS inb
    FROM canonical.interaction
   WHERE occurred_at > now() - make_interval(hours => $1)
     AND person_id IS NOT NULL
     AND body IS NOT NULL AND length(body) > 0
   GROUP BY person_id
)
SELECT r.person_id::text AS person_id, p.display_name,
       r.last_in, r.inb
  FROM recent r
  JOIN canonical.person p ON p.id = r.person_id
                          AND p.merged_into IS NULL
                          AND p.deleted_at IS NULL
 WHERE r.last_in IS NOT NULL
   AND (r.last_out IS NULL OR r.last_out < r.last_in)
 ORDER BY r.last_in DESC
 LIMIT $2
"""


# Calendar events overlapping the plan window [start, end). Declined and
# cancelled events are dropped. DISTINCT ON collapses the same meeting that
# appears on multiple of the user's accounts (same start + title) to one row,
# preferring the account that actually accepted. Source table is raw.gcal_event
# — may not exist on very old DBs, so the caller guards with a try/except.
GCAL_EVENTS_SQL = """
SELECT DISTINCT ON (e.start_ts, lower(coalesce(e.summary, '')))
       e.summary, e.location, e.start_ts, e.end_ts, e.all_day,
       e.self_response, e.account_email,
       jsonb_array_length(coalesce(e.attendees, '[]'::jsonb)) AS attendee_count
  FROM raw.gcal_event e
 WHERE e.start_ts >= $1 AND e.start_ts < $2
   AND coalesce(e.status, 'confirmed') <> 'cancelled'
   AND coalesce(e.self_response, 'accepted') <> 'declined'
 ORDER BY e.start_ts,
          lower(coalesce(e.summary, '')),
          (e.self_response = 'accepted') DESC
"""


async def gcal_events(conn: asyncpg.Connection, window_start, window_end) -> list[dict]:
    """Plan-window calendar events, time-ordered. Empty list (not an error)
    when the table is absent or no calendar has synced yet."""
    try:
        rows = await conn.fetch(GCAL_EVENTS_SQL, window_start, window_end)
    except asyncpg.UndefinedTableError:
        return []
    out = [_row(r) for r in rows]
    out.sort(key=lambda e: e.get("start_ts") or "")
    return out


async def gather(
    conn: asyncpg.Connection, cfg, *, window_start=None, window_end=None
) -> dict:
    """Pull every input the planner needs in one connection. Returns a
    plain dict of lists/ints, JSON-serializable (dates → isoformat).
    Calendar events are included when a plan window is supplied."""
    tasks = await conn.fetch(OPEN_TASKS_SQL, cfg.max_tasks)
    opps = await conn.fetch(LIVE_OPPS_SQL, cfg.max_opps)
    suggestions = await conn.fetch(TOP_SUGGESTIONS_SQL, 8)
    pending_count = await conn.fetchval(COUNT_PENDING_SUGGESTIONS_SQL)
    recaps = await conn.fetch(RECENT_RECAPS_SQL, 3, cfg.max_recaps)
    owes = await conn.fetch(OWES_REPLY_SQL, cfg.reply_window_hours, cfg.max_owes_reply)
    events = (
        await gcal_events(conn, window_start, window_end)
        if window_start is not None and window_end is not None
        else []
    )

    return {
        "tasks": [_row(r) for r in tasks],
        "opportunities": [_row(r) for r in opps],
        "suggestions": [_row(r) for r in suggestions],
        "owes_reply": [_row(r) for r in owes],
        "recaps": [_row(r) for r in recaps],
        "events": events,
        "counts": {
            "open_tasks": len(tasks),
            "live_opps": len(opps),
            "pending_suggestions": int(pending_count or 0),
            "owes_reply": len(owes),
            "events": len(events),
        },
    }


def _row(r: asyncpg.Record) -> dict:
    """Record → JSON-safe dict (datetime/date → ISO string)."""
    out: dict = {}
    for k, v in dict(r).items():
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


async def upsert_daily_plan(
    conn: asyncpg.Connection, *, plan_date, narrative: str | None, structured: dict
) -> str:
    """Idempotent on plan_date — regenerating overwrites the day's row."""
    row = await conn.fetchrow(
        """
        INSERT INTO memory.daily_plan (plan_date, narrative, structured, generated_at)
        VALUES ($1, $2, $3::jsonb, now())
        ON CONFLICT (plan_date) DO UPDATE SET
          narrative    = EXCLUDED.narrative,
          structured   = EXCLUDED.structured,
          generated_at = now()
        RETURNING id::text
        """,
        plan_date, narrative, json.dumps(structured, default=str),
    )
    return row["id"]
