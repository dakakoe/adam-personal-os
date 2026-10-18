from __future__ import annotations

import logging
from typing import Any

import anthropic
import asyncpg

from .config import Config
from . import local_llm, prompt

log = logging.getLogger(__name__)


# Candidate threads (watermark model): a genuine two-way conversation (both
# inbound AND outbound — filters newsletters) whose NEWEST message is newer
# than the last message we already scanned for it. $1 = seed_days (max
# staleness + the floor for never-scanned threads), $2 = per-run cap.
# Newest-activity first so fresh items surface fastest; un-scanned threads
# stay candidates next run (the cap delays, never drops).
ACTIVE_THREADS_SQL = """
WITH recent AS (
  SELECT person_id,
         count(*) FILTER (WHERE direction='inbound')  AS inb,
         count(*) FILTER (WHERE direction='outbound') AS outb,
         max(occurred_at) AS last_at,
         (array_agg(channel ORDER BY occurred_at DESC))[1] AS channel
    FROM canonical.interaction
   WHERE occurred_at > now() - make_interval(days => $1)
     AND person_id IS NOT NULL
     AND body IS NOT NULL AND length(body) > 0
   GROUP BY person_id
)
SELECT r.person_id::text AS person_id, p.display_name, p.sensitive, r.channel, r.inb, r.outb
  FROM recent r
  JOIN canonical.person p ON p.id = r.person_id
                          AND p.merged_into IS NULL
                          AND p.deleted_at IS NULL
  LEFT JOIN memory.interaction_scan_state st ON st.person_id = r.person_id
 WHERE r.inb > 0 AND r.outb > 0
   AND r.last_at > COALESCE(st.scanned_through, now() - make_interval(days => $1))
 ORDER BY r.last_at DESC
 LIMIT $2
"""

UPSERT_WATERMARK_SQL = """
INSERT INTO memory.interaction_scan_state (person_id, scanned_through, updated_at)
VALUES ($1::uuid, $2, now())
ON CONFLICT (person_id) DO UPDATE SET
  scanned_through = GREATEST(memory.interaction_scan_state.scanned_through, EXCLUDED.scanned_through),
  updated_at = now()
"""

THREAD_MESSAGES_SQL = """
SELECT occurred_at, direction, channel, body
  FROM canonical.interaction
 WHERE person_id = $1::uuid AND body IS NOT NULL AND length(body) > 0
 ORDER BY occurred_at DESC
 LIMIT $2
"""

# A person who belongs to exactly one project → auto-attribute. NULL when
# zero or ambiguous (multiple) memberships.
SOLE_PROJECT_SQL = """
SELECT project_id::text FROM memory.project_member WHERE person_id = $1::uuid
"""

# Dedup backstop: drop a candidate whose title is trigram-similar (>= the
# configured threshold) to ANY existing suggestion (pending/accepted/
# dismissed) OR non-deleted task for this person. Catches near-exact LLM
# re-proposals that exact-match dedup missed under title drift. The model
# also gets the already-tracked list (semantic dedup) for paraphrases that
# score below the threshold.
DEDUP_SQL = """
SELECT 1
  FROM (
    SELECT title FROM memory.suggestion WHERE person_id = $1::uuid
    UNION ALL
    SELECT title FROM memory.task
     WHERE with_person_id = $1::uuid AND deleted_at IS NULL
  ) t
 WHERE similarity(lower(btrim(t.title)), lower(btrim($2))) >= $3
 LIMIT 1
"""

# The person's already-tracked items, fed to the model so it won't re-
# propose them (even reworded). Suggestions of any status (respect
# dismissals) + live tasks, newest first, capped by the caller.
EXISTING_FOR_PERSON_SQL = """
SELECT title FROM (
  SELECT title, created_at FROM memory.suggestion WHERE person_id = $1::uuid
  UNION ALL
  SELECT title, created_at FROM memory.task
   WHERE with_person_id = $1::uuid AND deleted_at IS NULL
) t
ORDER BY created_at DESC
LIMIT $2
"""

INSERT_SUGGESTION_SQL = """
INSERT INTO memory.suggestion
  (kind, title, detail, project_id, person_id, suggested_stage,
   estimated_value, confidence, source_kind, source_ref, context_at)
VALUES ($1, $2, $3, $4::uuid, $5::uuid, $6,
        $7, $8, 'interaction', $9, $10)
"""


def _discussed_at(item: dict, fallback):
    """When this item was discussed: parse the model's last_discussed
    (exact 'YYYY-MM-DD HH:MM' UTC, or just the date) → tz-aware datetime,
    else fall back to the thread's latest message time."""
    from datetime import datetime, timezone
    raw = (item.get("last_discussed") or "").strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw[:len("2026-01-01 00:00:00")], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return fallback


async def _sole_project(conn: asyncpg.Connection, person_id: str) -> str | None:
    rows = await conn.fetch(SOLE_PROJECT_SQL, person_id)
    ids = [r["project_id"] for r in rows if r["project_id"]]
    return ids[0] if len(ids) == 1 else None


async def _exists(conn: asyncpg.Connection, person_id: str, title: str, threshold: float) -> bool:
    return await conn.fetchrow(DEDUP_SQL, person_id, title, threshold) is not None


async def _existing_titles(conn: asyncpg.Connection, person_id: str, limit: int) -> list[str]:
    rows = await conn.fetch(EXISTING_FOR_PERSON_SQL, person_id, limit)
    return [r["title"] for r in rows if r["title"]]


def _extract(client: anthropic.Anthropic, cfg: Config, payload: str) -> dict[str, Any] | None:
    resp = client.messages.create(
        model=cfg.model,
        max_tokens=1200,
        system=prompt.SYSTEM_PROMPT,
        tools=[prompt.SAVE_TOOL],
        tool_choice={"type": "tool", "name": "record_conversation_items"},
        messages=[{"role": "user", "content": payload}],
    )
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "record_conversation_items":
            return dict(block.input)
    return None


def _extract_local(cfg: Config, payload: str) -> dict[str, Any] | None:
    """Sensitive-contact path: same system prompt, on-box Ollama, in-prompt
    JSON schema instead of the forced tool call. Raises on Ollama failure —
    the caller skips the thread (fail-closed; NEVER falls back to cloud)."""
    text = local_llm.ollama_chat(
        url=cfg.ollama_url, model=cfg.ollama_model,
        system=prompt.SYSTEM_PROMPT + local_llm.JSON_INSTRUCTION,
        prompt=payload, json_mode=True, temperature=0.0,
        num_predict=700, timeout=cfg.ollama_timeout)
    return local_llm.parse_items(text)


def run(cfg: Config) -> int:
    import asyncio
    return asyncio.run(_run_async(cfg))


async def _run_async(cfg: Config) -> int:
    pool = await asyncpg.create_pool(cfg.db_url, min_size=1, max_size=2, statement_cache_size=0)
    client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)
    threads_scanned = tasks_added = opps_added = skipped_dupe = empty = 0
    try:
        async with pool.acquire() as conn:
            threads = await conn.fetch(ACTIVE_THREADS_SQL, cfg.seed_days, cfg.max_threads)
        log.info(
            "interaction-scan: %d threads with new activity (seed_days=%d cap=%d model=%s dry_run=%s)",
            len(threads), cfg.seed_days, cfg.max_threads, cfg.model, cfg.dry_run,
        )

        for t in threads:
            person_id = t["person_id"]
            async with pool.acquire() as conn:
                msg_rows = await conn.fetch(THREAD_MESSAGES_SQL, person_id, cfg.messages_per_thread)
                existing = await _existing_titles(conn, person_id, cfg.max_existing_items)
            if len(msg_rows) < 2:
                continue
            messages = list(reversed([dict(m) for m in msg_rows]))
            # Fallback "discussed" time when the model can't pin a date: the
            # thread's most recent message.
            occ_times = [m["occurred_at"] for m in messages if m.get("occurred_at") is not None]
            thread_latest = max(occ_times) if occ_times else None
            payload = prompt.render(
                contact_name=t["display_name"], channel=t["channel"],
                messages=messages, self_label=cfg.self_label,
                existing_items=existing,
            )
            threads_scanned += 1

            sensitive = bool(t["sensitive"])
            try:
                # sync clients off the event loop
                import asyncio
                if sensitive:
                    log.info("route=local person=%s reason=sensitive", t["display_name"])
                    result = await asyncio.to_thread(_extract_local, cfg, payload)
                else:
                    log.info("route=cloud person=%s", t["display_name"])
                    result = await asyncio.to_thread(_extract, client, cfg, payload)
            except Exception:
                if sensitive:
                    # fail-closed: skip BEFORE the watermark advances so the
                    # thread is re-scanned next run; never retry via cloud.
                    log.exception("local extract FAILED for %s — skipped, no cloud fallback",
                                  t["display_name"])
                else:
                    log.exception("extraction failed for %s", t["display_name"])
                continue

            # Thread was successfully read → advance its watermark to the
            # latest message, even if nothing was extracted, so we never
            # re-scan unchanged messages. (Skipped on dry runs.)
            if not cfg.dry_run and thread_latest is not None:
                async with pool.acquire() as conn:
                    await conn.execute(UPSERT_WATERMARK_SQL, person_id, thread_latest)

            if not result:
                empty += 1
                continue

            items_tasks = result.get("tasks") or []
            items_opps = result.get("opportunities") or []
            if not items_tasks and not items_opps:
                empty += 1
                continue

            async with pool.acquire() as conn:
                project_id = await _sole_project(conn, person_id)

                for it in items_tasks:
                    title = (it.get("title") or "").strip()
                    if not title:
                        continue
                    if cfg.dry_run:
                        log.info("DRY task[%s]: %s", t["display_name"], title)
                        tasks_added += 1
                        continue
                    if await _exists(conn, person_id, title, cfg.dedup_similarity):
                        skipped_dupe += 1
                        continue
                    await conn.execute(
                        INSERT_SUGGESTION_SQL,
                        "task", title, None, project_id, person_id,
                        None, None, "medium", person_id,
                        _discussed_at(it, thread_latest),
                    )
                    tasks_added += 1

                for it in items_opps:
                    title = (it.get("title") or "").strip()
                    if not title:
                        continue
                    if cfg.dry_run:
                        log.info("DRY opp[%s]: %s (%s)", t["display_name"], title, it.get("stage"))
                        opps_added += 1
                        continue
                    if await _exists(conn, person_id, title, cfg.dedup_similarity):
                        skipped_dupe += 1
                        continue
                    await conn.execute(
                        INSERT_SUGGESTION_SQL,
                        "opportunity", title, None, project_id, person_id,
                        it.get("stage") or "intro", it.get("value") or None,
                        "medium", person_id,
                        _discussed_at(it, thread_latest),
                    )
                    opps_added += 1

        log.info(
            "interaction-scan done: threads=%d tasks=%d opps=%d skipped_dupe=%d empty=%d",
            threads_scanned, tasks_added, opps_added, skipped_dupe, empty,
        )
        return 0
    finally:
        await pool.close()
