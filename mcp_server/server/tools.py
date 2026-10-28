from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import db, embed, findpeople, rank, sensitive
from .config import Config

log = logging.getLogger(__name__)

# Every query that returns people, messages or mail lives at module level so
# tests/test_sensitive.py can prove each one carries the sensitive-contact
# guard — and so eval/find_people_eval.py scores exactly what the tool runs.
_NOT_SENSITIVE = sensitive.person_clause()

_TELEGRAM_HANDLE_CTE = """
WITH tg AS (
  SELECT DISTINCT ON (i.person_id)
         i.person_id, i.source_id AS telegram_id, u.username AS telegram_username
  FROM canonical.identity i
  LEFT JOIN raw.telegram_user u ON u.source_user_id = i.source_id::bigint
  WHERE i.source = 'telegram'
  ORDER BY i.person_id, i.id
)"""

SEARCH_PEOPLE_SQL = f"""{_TELEGRAM_HANDLE_CTE}
SELECT p.id AS person_id,
       p.display_name,
       tg.telegram_id,
       tg.telegram_username,
       (SELECT count(*) FROM canonical.interaction
          WHERE person_id = p.id) AS total_interactions,
       (SELECT max(occurred_at) FROM canonical.interaction
          WHERE person_id = p.id) AS last_interaction_at
FROM canonical.person p
LEFT JOIN tg ON tg.person_id = p.id
WHERE p.merged_into IS NULL
  AND {_NOT_SENSITIVE}
  AND p.display_name ILIKE '%' || $1 || '%'
ORDER BY last_interaction_at DESC NULLS LAST
LIMIT $2
"""

TOP_CONTACTS_SQL = f"""{_TELEGRAM_HANDLE_CTE}
SELECT p.id AS person_id,
       p.display_name,
       tg.telegram_id,
       tg.telegram_username,
       count(*)::bigint AS total_interactions,
       max(i.occurred_at) AS last_interaction_at
FROM canonical.interaction i
JOIN canonical.person p ON p.id = i.person_id
LEFT JOIN tg ON tg.person_id = p.id
WHERE p.merged_into IS NULL
  AND {_NOT_SENSITIVE}
GROUP BY p.id, p.display_name, tg.telegram_id, tg.telegram_username
ORDER BY total_interactions DESC
LIMIT $1
"""

SEARCH_MESSAGES_SQL = f"""
SELECT i.occurred_at,
       i.direction,
       i.channel,
       p.display_name,
       p.id AS person_id,
       i.body
FROM canonical.interaction i
LEFT JOIN canonical.person p
       ON p.id = COALESCE(i.person_id, i.author_person_id)
WHERE i.body ILIKE '%' || $1 || '%'
  AND {sensitive.INTERACTION_CLAUSE}
ORDER BY i.occurred_at DESC
LIMIT $2
"""

_SEMANTIC_MESSAGE_SELECT = """
SELECT i.id::text AS interaction_id,
       i.occurred_at,
       i.direction,
       i.channel,
       p.display_name,
       p.id::text AS person_id,
       i.body,
       (e.embedding <=> $1) AS distance
FROM memory.interaction_embedding e
JOIN canonical.interaction i ON i.id = e.interaction_id
LEFT JOIN canonical.person p
       ON p.id = COALESCE(i.person_id, i.author_person_id)"""

SEMANTIC_MESSAGES_SQL = f"""{_SEMANTIC_MESSAGE_SELECT}
WHERE {sensitive.INTERACTION_CLAUSE}
ORDER BY e.embedding <=> $1
LIMIT $2
"""

SEMANTIC_MESSAGES_BY_PERSON_SQL = f"""{_SEMANTIC_MESSAGE_SELECT}
WHERE COALESCE(i.person_id, i.author_person_id) = $2::uuid
  AND {sensitive.INTERACTION_CLAUSE}
ORDER BY e.embedding <=> $1
LIMIT $3
"""

SEMANTIC_MAIL_SQL = f"""
SELECT m.id AS gmail_message_id,
       m.internal_date,
       m.account_email,
       m.from_address,
       m.from_name,
       m.subject,
       m.body_text,
       (e.embedding <=> $1) AS distance
FROM memory.mail_embedding e
JOIN raw.gmail_message m ON m.id = e.gmail_message_id
WHERE ($2::text IS NULL OR m.account_email = $2)
  AND {sensitive.MAIL_CLAUSE}
ORDER BY e.embedding <=> $1
LIMIT $3
"""

# Direct messages (person_id) plus the person's own posts in followed groups
# (author_person_id, person_id NULL). The group's name comes from the raw
# Telegram row; other channels have no group concept and stay "dm".
RECENT_INTERACTIONS_SQL = """
SELECT i.id::text AS interaction_id,
       i.occurred_at,
       i.direction,
       i.channel,
       CASE WHEN i.person_id = $1::uuid THEN 'dm' ELSE 'group' END AS role,
       CASE WHEN i.person_id = $1::uuid THEN NULL ELSE g.title END AS in_group,
       i.body
FROM canonical.interaction i
LEFT JOIN raw.telegram_message tm
       ON i.raw_source = 'raw.telegram_message' AND tm.id = i.raw_id AND i.person_id IS NULL
LEFT JOIN raw.telegram_group_allowlist g ON g.chat_id = tm.chat_id
WHERE i.person_id = $1::uuid OR i.author_person_id = $1::uuid
ORDER BY i.occurred_at DESC
LIMIT $2
"""

READ_MESSAGE_SQL = f"""
SELECT i.id::text AS interaction_id,
       i.occurred_at,
       i.direction,
       i.channel,
       p.display_name,
       p.id::text AS person_id,
       i.body
FROM canonical.interaction i
LEFT JOIN canonical.person p
       ON p.id = COALESCE(i.person_id, i.author_person_id)
WHERE i.id = $1::uuid
  AND {sensitive.INTERACTION_CLAUSE}
"""

READ_MAIL_SQL = f"""
SELECT m.id AS gmail_message_id,
       m.internal_date,
       m.account_email,
       m.from_address,
       m.from_name,
       m.to_addresses,
       m.subject,
       m.body_text
FROM raw.gmail_message m
WHERE m.id = $1::bigint
  AND {sensitive.MAIL_CLAUSE}
"""

MAX_CHARS_CAP = 20000


def _chars(max_chars: int) -> int:
    """Per-result body budget: lists default to an excerpt, but a caller
    that needs to read can ask for the whole message (up to the cap)."""
    return max(100, min(int(max_chars or 500), MAX_CHARS_CAP))


SEMANTIC_PEOPLE_SQL = f"""
SELECT p.id::text AS person_id,
       p.display_name,
       mp.summary,
       (mp.embedding <=> $1) AS distance
FROM memory.profile mp
JOIN canonical.person p ON p.id = mp.person_id
WHERE mp.embedding IS NOT NULL
  AND p.merged_into IS NULL
  AND {_NOT_SENSITIVE}
ORDER BY mp.embedding <=> $1
LIMIT $2
"""


def register(mcp: FastMCP, cfg: Config) -> None:
    """Register all v1 memory tools on the given FastMCP server."""
    excluded = list(cfg.excluded_circles)

    @mcp.tool()
    async def search_people(query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Find people in the contact memory by display name (case-insensitive
        substring match). Returns up to `limit` matches, sorted by most-recent
        interaction first.

        Use this to translate a name fragment ('alex', 'smith') into the
        canonical person_id needed by the other tools.
        """
        pool = await db.pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(SEARCH_PEOPLE_SQL, query, max(1, min(limit, 100)))
        return [db._row_to_dict(r) for r in rows]

    @mcp.tool()
    async def person_summary(person_id: str) -> dict[str, Any]:
        """Aggregate stats for a person: direct-message totals (inbound/
        outbound, first/last, channels), their posts in followed group chats
        (group_posts, last_group_post_at), plus identity info.

        Pass the `person_id` returned by `search_people` or `top_contacts`.
        """
        pool = await db.pool()
        async with pool.acquire() as conn:
            base = await conn.fetchrow(
                """
                SELECT p.id AS person_id,
                       p.display_name,
                       p.notes,
                       tg.source_id AS telegram_id,
                       u.username   AS telegram_username,
                       u.phone      AS phone,
                       u.about      AS telegram_bio
                FROM canonical.person p
                LEFT JOIN LATERAL (
                  SELECT i.source_id
                  FROM canonical.identity i
                  WHERE i.person_id = p.id AND i.source = 'telegram'
                  ORDER BY i.id
                  LIMIT 1
                ) tg ON true
                LEFT JOIN raw.telegram_user u
                  ON tg.source_id IS NOT NULL
                 AND u.source_user_id = tg.source_id::bigint
                WHERE p.id = $1::uuid
                """,
                person_id,
            )
            if base is None:
                return {"error": f"no person with id={person_id}"}
            if await sensitive.is_sensitive(conn, person_id):
                return sensitive.redacted(person_id)
            stats = await conn.fetchrow(
                """
                SELECT count(*)::bigint                              AS total_interactions,
                       count(*) FILTER (WHERE direction='inbound')   AS inbound_count,
                       count(*) FILTER (WHERE direction='outbound')  AS outbound_count,
                       min(occurred_at)                              AS first_interaction_at,
                       max(occurred_at)                              AS last_interaction_at,
                       array_agg(DISTINCT channel ORDER BY channel)  AS channels
                FROM canonical.interaction
                WHERE person_id = $1::uuid
                """,
                person_id,
            )
            # What they said in followed group chats — a person you have never
            # messaged directly can still be very present.
            group_stats = await conn.fetchrow(
                """
                SELECT count(*)::bigint AS group_posts, max(occurred_at) AS last_group_post_at
                FROM canonical.interaction
                WHERE author_person_id = $1::uuid AND person_id IS NULL
                """,
                person_id,
            )
            # Structured extract from the profile builder (Haiku tool-call).
            # Carries current_company, current_role, personal_emails, etc.
            structured_row = await conn.fetchrow(
                """
                SELECT structured
                FROM memory.profile
                WHERE person_id = $1::uuid
                """,
                person_id,
            )
            # Extracted signals: best (highest-confidence, bio-preferred) row
            # per (signal_type, value). Caller gets a flat list ordered the
            # same way the Twenty sync ranks them.
            signal_rows = await conn.fetch(
                """
                WITH ranked AS (
                  SELECT signal_type, value, confidence, source,
                         ROW_NUMBER() OVER (
                           PARTITION BY signal_type, value
                           ORDER BY
                             CASE confidence WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                             CASE source WHEN 'telegram_bio' THEN 0 ELSE 1 END,
                             last_seen_at DESC
                         ) AS rn
                  FROM memory.extracted_signal
                  WHERE person_id = $1::uuid
                )
                SELECT signal_type, value, confidence, source
                FROM ranked
                WHERE rn = 1
                ORDER BY
                  CASE confidence WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                  signal_type, value
                """,
                person_id,
            )
        out = db._row_to_dict(base)
        out.update(db._row_to_dict(stats))
        out.update(db._row_to_dict(group_stats))
        # structured can be a string (JSONB serialized) or dict depending on
        # driver registration; normalize to dict for the caller.
        structured: Any = None
        if structured_row and structured_row["structured"] is not None:
            raw = structured_row["structured"]
            if isinstance(raw, str):
                import json as _json
                try:
                    structured = _json.loads(raw)
                except Exception:
                    structured = None
            else:
                structured = raw
        out["structured"] = structured
        out["signals"] = [db._row_to_dict(r) for r in signal_rows]
        return out

    @mcp.tool()
    async def recent_interactions(
        person_id: str, limit: int = 20, max_chars: int = 500
    ) -> list[dict[str, Any]]:
        """Most recent N things said between the user and a person, newest
        first: direct messages (role "dm") AND what the person posted in
        group chats the user follows (role "group", with `in_group` = the
        chat's name). Returns interaction_id, timestamp, direction, channel,
        and the body cut to `max_chars` (default 500 — raise it, up to 20000,
        when you need to read rather than scan; or call read_message for one).
        A person who only ever writes in groups still shows up here.

        Body is NULL for voice messages until the Whisper transcription
        worker has run; treat NULL as 'voice not yet transcribed'.
        """
        pool = await db.pool()
        async with pool.acquire() as conn:
            if await sensitive.is_sensitive(conn, person_id):
                return [sensitive.redacted(person_id)]
            rows = await conn.fetch(RECENT_INTERACTIONS_SQL, person_id, max(1, min(limit, 200)))
        return [
            {
                **db._row_to_dict(r),
                "body": db._truncate(r["body"], _chars(max_chars)),
            }
            for r in rows
        ]

    @mcp.tool()
    async def search_messages(query: str, limit: int = 20, max_chars: int = 500) -> list[dict[str, Any]]:
        """Substring search across all message bodies (ILIKE). Returns each
        match with the counterparty's display name, timestamp, direction,
        channel, and the body cut to `max_chars` (default 500; up to 20000).
        Use this for 'find messages about <topic>' style queries.

        Limitations: case-insensitive substring only, no stemming or fuzzy
        match. Semantic search will come once embeddings are built.
        """
        pool = await db.pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(SEARCH_MESSAGES_SQL, query, max(1, min(limit, 100)))
        return [
            {
                **db._row_to_dict(r),
                "body": db._truncate(r["body"], _chars(max_chars)),
            }
            for r in rows
        ]

    @mcp.tool()
    async def top_contacts(limit: int = 20) -> list[dict[str, Any]]:
        """Top people by total interaction count across the entire memory.
        Useful as a 'who do I talk to most' starting point.
        """
        pool = await db.pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(TOP_CONTACTS_SQL, max(1, min(limit, 100)))
        return [db._row_to_dict(r) for r in rows]

    @mcp.tool()
    async def get_person_profile(person_id: str) -> dict[str, Any]:
        """LLM-generated narrative summary of a person — who they are,
        how the user knows them, what's been discussed. Read this for
        'tell me about <name>' style queries; it's a paragraph, not
        structured data. Returns null `summary` if no profile has been
        built yet (the builder backfill is ongoing and prioritises
        most-active contacts first)."""
        pool = await db.pool()
        async with pool.acquire() as conn:
            if await sensitive.is_sensitive(conn, person_id):
                return sensitive.redacted(person_id)
            row = await conn.fetchrow(
                """
                SELECT p.id::text AS person_id,
                       p.display_name,
                       mp.summary,
                       mp.source_interaction_count,
                       mp.last_built_at
                FROM canonical.person p
                LEFT JOIN memory.profile mp ON mp.person_id = p.id
                WHERE p.id = $1::uuid
                """,
                person_id,
            )
        if row is None:
            return {"error": f"no person with id={person_id}"}
        return db._row_to_dict(row)

    @mcp.tool()
    async def semantic_search_people(
        query: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Find people by *description* rather than by name. Encodes the
        query with the same model the profile builder used and ranks by
        cosine distance against memory.profile.embedding.

        Use this for prompts like 'who works in crypto', 'friends in
        Bali', 'people I haven't talked to in a year'. For name lookups
        use `search_people`; the two tools complement.

        Only persons whose profile has been built so far are searchable
        — coverage grows over time as the profile backfill catches up."""
        qvec = await embed.encode_query(query)
        pool = await db.pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                SEMANTIC_PEOPLE_SQL,
                qvec,
                max(1, min(limit, 50)),
            )
        return [
            {
                **db._row_to_dict(r),
                "summary": db._truncate(r["summary"], 600),
                "distance": round(float(r["distance"]), 4),
            }
            for r in rows
        ]

    try:
        catalog = db.fetch_at_startup(findpeople.CATALOG_SQL, excluded)
    except Exception:  # noqa: BLE001 — never block startup on a description
        log.warning("find_people: could not read circles for the description", exc_info=True)
        catalog = []

    @mcp.tool(description=findpeople.describe(catalog))
    async def find_people(
        need: str,
        circles: list[str] | None = None,
        scope: str = "known",
        limit: int = findpeople.DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        if scope not in findpeople.SCOPES:
            return {"error": f"scope must be one of {list(findpeople.SCOPES)}"}
        requested = findpeople.normalize_circles(circles)
        qvec = await embed.encode_query(need)
        pool = await db.pool()
        async with pool.acquire() as conn:
            matched = await findpeople.resolve_circles(conn, requested, excluded)
            rows = await findpeople.rank(
                conn, qvec,
                circle_keys=[m["key"] for m in matched],
                excluded=excluded,
                include_strangers=(scope == "all"),
                limit=max(1, min(limit, findpeople.MAX_LIMIT)),
            )
            # Sensitive contacts are never even queried for evidence.
            ev = await findpeople.evidence(
                conn, qvec, [r["person_id"] for r in rows if not r["sensitive"]])
            out: dict[str, Any] = {
                "circles_matched": [m["label"] for m in matched],
                "results": [findpeople.shape(r, ev.get(r["person_id"], [])) for r in rows],
            }
            unknown = findpeople.unmatched(requested, matched)
            if unknown:
                # The description's list is from startup; this one is live.
                out["circles_unknown"] = unknown
                out["circles_available"] = [
                    c["label"] for c in await findpeople.circle_catalog(conn, excluded)]
        return out

    @mcp.tool()
    async def semantic_search_messages(
        query: str,
        limit: int = 20,
        person_id: str | None = None,
        max_chars: int = 500,
    ) -> list[dict[str, Any]]:
        """Semantic search across messages. Encodes the query with the
        same multilingual-e5-base model the embedder used, then ranks by
        cosine distance against memory.interaction_embedding.

        Use this for fuzzy/topical queries that substring search can't
        handle — 'conversations about money', 'when we discussed travel'.
        For exact phrase or named-entity matches, use `search_messages`
        which does ILIKE.

        Optional `person_id` filters to a single counterparty. Body is cut
        to `max_chars` per result (default 500; up to 20000, or read_message
        for one message in full).

        Note: only embedded interactions are searchable. The embedding
        backlog runs continuously; missing recent messages will appear
        after the next batch processes them.
        """
        qvec = await embed.encode_query(query)
        pool = await db.pool()
        async with pool.acquire() as conn:
            if person_id:
                rows = await conn.fetch(
                    SEMANTIC_MESSAGES_BY_PERSON_SQL, qvec, person_id, max(1, min(limit, 100)))
            else:
                rows = await conn.fetch(SEMANTIC_MESSAGES_SQL, qvec, max(1, min(limit, 100)))
        return [
            {
                **db._row_to_dict(r),
                "body": db._truncate(r["body"], _chars(max_chars)),
                # distance is cosine; lower = closer. Round for readability.
                "distance": round(float(r["distance"]), 4),
            }
            for r in rows
        ]

    @mcp.tool()
    async def semantic_search_mail(
        query: str,
        limit: int = 20,
        account_email: str | None = None,
        max_chars: int = 500,
    ) -> list[dict[str, Any]]:
        """Semantic search across EMAIL bodies (raw.gmail_message), same
        e5-base embedding space as semantic_search_messages. Ranks by cosine
        distance against memory.mail_embedding. Optional `account_email`
        narrows to one mailbox. Body cut to `max_chars` per result (default
        500; up to 20000, or read_mail for one message in full).

        Note: only embedded mail is searchable — the backlog drains in the
        embedder's idle gaps; very recent mail appears after the next pass.
        """
        qvec = await embed.encode_query(query)
        pool = await db.pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                SEMANTIC_MAIL_SQL, qvec, account_email, max(1, min(limit, 100)))
        return [
            {
                **{k: v for k, v in db._row_to_dict(r).items() if k != "body_text"},
                "body": db._truncate(r["body_text"], _chars(max_chars)),
                "distance": round(float(r["distance"]), 4),
            }
            for r in rows
        ]

    @mcp.tool()
    async def read_message(interaction_id: str) -> dict[str, Any]:
        """One message in full (no excerpt cut): the interaction_id from
        recent_interactions / search_messages / semantic_search_messages.
        Sensitive contacts' messages are not returned."""
        pool = await db.pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(READ_MESSAGE_SQL, interaction_id)
        if row is None:
            return {"error": f"no readable message with id={interaction_id}"}
        return db._row_to_dict(row)

    @mcp.tool()
    async def read_mail(gmail_message_id: int) -> dict[str, Any]:
        """One email in full: the gmail_message_id from semantic_search_mail.
        Mail touching a sensitive contact is not returned."""
        try:
            mid = int(gmail_message_id)
        except (TypeError, ValueError):
            return {"error": f"gmail_message_id must be the number from semantic_search_mail, got {gmail_message_id!r}"}
        pool = await db.pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(READ_MAIL_SQL, mid)
        if row is None:
            return {"error": f"no readable mail with id={gmail_message_id}"}
        d = db._row_to_dict(row)
        d["body"] = d.pop("body_text")
        return d

    @mcp.tool()
    async def local_ask(question: str, person_id: str | None = None, k: int = 12) -> dict[str, Any]:
        """Answer a question about the message archive using ONLY the local LLM
        (Ollama on this server) — the content and the answer never leave the box.
        Retrieval: semantic top-k over embedded interactions AND embedded MAIL
        bodies, merged by distance (sources carry kind: interaction|mail). With
        person_id, interactions scope to that person and mail scopes to their
        email identities (skipped if they have none). A Gmail message can appear
        both as mail and as its interaction twin — corroborating excerpts, fine.

        Use this for private/sensitive questions that shouldn't reach a cloud
        model. Trade-off: it's SLOW (~30–90s on CPU) and a small model — for hard
        reasoning over non-sensitive data, prefer semantic_search_messages /
        semantic_search_mail and reason in the calling model instead.
        """
        import httpx

        from . import config as config_mod
        cfg = config_mod.load()
        k = max(1, min(k, 20))

        qvec = await embed.encode_query(question)
        pool = await db.pool()
        async with pool.acquire() as conn:
            if person_id:
                rows = await conn.fetch(
                    """
                    SELECT i.occurred_at, i.direction, i.channel, p.display_name, i.body,
                           (e.embedding <=> $1) AS distance
                    FROM memory.interaction_embedding e
                    JOIN canonical.interaction i ON i.id = e.interaction_id
                    LEFT JOIN canonical.person p
                       ON p.id = COALESCE(i.person_id, i.author_person_id)
                    WHERE COALESCE(i.person_id, i.author_person_id) = $2::uuid
                    ORDER BY e.embedding <=> $1 LIMIT $3
                    """, qvec, person_id, k)
                emails = [
                    r["source_id"].lower() for r in await conn.fetch(
                        "SELECT source_id FROM canonical.identity "
                        "WHERE person_id = $1::uuid AND source = 'email'", person_id)
                    if r["source_id"]
                ]
                mail_rows = []
                if emails:
                    mail_rows = await conn.fetch(
                        """
                        SELECT m.internal_date, m.from_address, m.from_name, m.subject,
                               m.body_text, (e.embedding <=> $1) AS distance
                        FROM memory.mail_embedding e
                        JOIN raw.gmail_message m ON m.id = e.gmail_message_id
                        WHERE (lower(m.from_address) = ANY($2::text[])
                               OR EXISTS (SELECT 1 FROM unnest(m.to_addresses) a
                                           WHERE lower(a) = ANY($2::text[])))
                        ORDER BY e.embedding <=> $1 LIMIT $3
                        """, qvec, emails, k)
            else:
                rows = await conn.fetch(
                    """
                    SELECT i.occurred_at, i.direction, i.channel, p.display_name, i.body,
                           (e.embedding <=> $1) AS distance
                    FROM memory.interaction_embedding e
                    JOIN canonical.interaction i ON i.id = e.interaction_id
                    LEFT JOIN canonical.person p
                       ON p.id = COALESCE(i.person_id, i.author_person_id)
                    ORDER BY e.embedding <=> $1 LIMIT $2
                    """, qvec, k)
                mail_rows = await conn.fetch(
                    """
                    SELECT m.internal_date, m.from_address, m.from_name, m.subject,
                           m.body_text, (e.embedding <=> $1) AS distance
                    FROM memory.mail_embedding e
                    JOIN raw.gmail_message m ON m.id = e.gmail_message_id
                    ORDER BY e.embedding <=> $1 LIMIT $2
                    """, qvec, k)
        merged = rank.merge_by_distance(rows, mail_rows, k)
        if not merged:
            return {"answer": "No embedded messages matched the question.",
                    "model": cfg.ollama_model, "sources": []}

        excerpts, sources = [], []
        for kind, r in merged:
            excerpt, source = (rank.format_mail_excerpt(r) if kind == "mail"
                               else rank.format_interaction_excerpt(r))
            excerpts.append(excerpt)
            sources.append(source)

        prompt = (
            "You are a private assistant answering from the user's own message archive.\n"
            "Answer the question using ONLY the excerpts below. Mention dates and names "
            "when relevant. If the excerpts don't contain the answer, say so plainly.\n\n"
            "Excerpts:\n" + "\n".join(excerpts) + f"\n\nQuestion: {question}"
        )
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{cfg.ollama_url.rstrip('/')}/api/chat", timeout=180.0,
                    json={"model": cfg.ollama_model, "stream": False,
                          "options": {"temperature": 0.2, "num_predict": 350},
                          "messages": [{"role": "user", "content": prompt}]})
                resp.raise_for_status()
                answer = resp.json().get("message", {}).get("content", "").strip()
        except Exception as e:  # noqa: BLE001
            log.warning("local_ask ollama call failed: %s", e)
            return {"error": f"local model unavailable: {e}", "sources": sources}

        return {"answer": answer, "model": cfg.ollama_model, "sources": sources}
