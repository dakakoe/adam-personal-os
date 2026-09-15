from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import asyncpg


async def overall_stats(conn: asyncpg.Connection, since: datetime) -> dict[str, Any]:
    row = await conn.fetchrow(
        """
        SELECT
          count(*)                                          AS messages,
          count(*) FILTER (WHERE direction='inbound')       AS inbound,
          count(*) FILTER (WHERE direction='outbound')      AS outbound,
          count(DISTINCT person_id)                         AS people,
          count(*) FILTER (WHERE channel='telegram_voice')  AS voice_msgs,
          count(*) FILTER (WHERE channel='telegram_voice' AND body IS NOT NULL) AS voice_transcribed
        FROM canonical.interaction
        WHERE occurred_at >= $1
        """,
        since,
    )
    return dict(row)


async def top_contacts(
    conn: asyncpg.Connection, since: datetime, *, limit: int = 10
) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT p.display_name,
               p.id::text AS person_id,
               count(*)                                      AS messages,
               count(*) FILTER (WHERE i.direction='inbound') AS inbound,
               count(*) FILTER (WHERE i.direction='outbound')AS outbound
        FROM canonical.interaction i
        JOIN canonical.person p ON p.id = i.person_id
        WHERE i.occurred_at >= $1 AND p.merged_into IS NULL
        GROUP BY p.id, p.display_name
        ORDER BY messages DESC
        LIMIT $2
        """,
        since, limit,
    )
    return [dict(r) for r in rows]


async def new_voice_transcripts(
    conn: asyncpg.Connection, since: datetime, *, limit: int = 5
) -> list[dict[str, Any]]:
    """Voice messages whose transcripts landed in the window (regardless of
    when the message itself was sent). These are the 'I forgot about that
    voice memo from 3 weeks ago' moments."""
    rows = await conn.fetch(
        """
        SELECT i.id::text AS interaction_id,
               i.occurred_at,
               p.display_name,
               substring(i.body, 1, 240) AS preview
        FROM canonical.interaction i
        JOIN canonical.person p ON p.id = i.person_id
        WHERE i.channel = 'telegram_voice'
          AND i.body IS NOT NULL
          AND i.created_at >= $1
          AND p.merged_into IS NULL
        ORDER BY i.created_at DESC
        LIMIT $2
        """,
        since, limit,
    )
    return [dict(r) for r in rows]


async def re_engagement_candidates(
    conn: asyncpg.Connection, *, limit: int = 5
) -> list[dict[str, Any]]:
    """People we've talked to a lot historically but not in a while. The
    'follow up with' list. Definition: more than 50 total interactions,
    last interaction was 30-180 days ago (not 'totally cold', but not
    'just spoke yesterday')."""
    rows = await conn.fetch(
        """
        SELECT p.display_name,
               p.id::text AS person_id,
               s.total,
               s.last_at,
               (now() - s.last_at)::interval AS gap
        FROM canonical.person p
        JOIN LATERAL (
          SELECT count(*) AS total, max(occurred_at) AS last_at
          FROM canonical.interaction
          WHERE person_id = p.id
        ) s ON true
        WHERE p.merged_into IS NULL
          AND s.total > 50
          AND s.last_at < now() - interval '30 days'
          AND s.last_at > now() - interval '180 days'
        ORDER BY s.total DESC
        LIMIT $1
        """,
        limit,
    )
    return [dict(r) for r in rows]


async def memory_totals(conn: asyncpg.Connection) -> dict[str, Any]:
    row = await conn.fetchrow(
        """
        SELECT
          (SELECT count(*) FROM canonical.person      WHERE merged_into IS NULL) AS persons,
          (SELECT count(*) FROM canonical.interaction)                            AS interactions,
          (SELECT count(*) FROM canonical.interaction WHERE body IS NOT NULL)     AS bodies,
          (SELECT count(*) FROM memory.profile)                                    AS profiles,
          (SELECT count(*) FROM memory.interaction_embedding)                      AS embeddings
        """
    )
    return dict(row)


async def newest_contacts_in_window(
    conn: asyncpg.Connection, since: datetime, *, limit: int = 5
) -> list[dict[str, Any]]:
    """Persons whose FIRST EVER interaction landed inside the window —
    i.e. people you started talking to today."""
    rows = await conn.fetch(
        """
        SELECT p.display_name, p.id::text AS person_id,
               s.first_at, s.total
        FROM canonical.person p
        JOIN LATERAL (
          SELECT count(*) AS total, min(occurred_at) AS first_at
          FROM canonical.interaction
          WHERE person_id = p.id
        ) s ON true
        WHERE p.merged_into IS NULL
          AND s.first_at >= $1
        ORDER BY s.first_at DESC
        LIMIT $2
        """,
        since, limit,
    )
    return [dict(r) for r in rows]
