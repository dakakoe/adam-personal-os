"""All SQL lives here, called from the routes. Keeps the route handlers
thin and the queries reviewable in isolation."""

from __future__ import annotations

import calendar
import json
import re
import uuid
from datetime import date, timedelta
from typing import Any

import asyncpg


# --- shared SQL fragments ----------------------------------------------
# These are interpolated into the queue / pending / cleanup SQL strings
# via f-strings. Module load order matters → defined here near the top,
# before any consumer.

# Reusable EXISTS that fires when two persons have *incompatible* identities
# on a "one account per person" channel (telegram + linkedin). If both sides
# own a telegram identity with different source_ids, they own different
# Telegram accounts → can't be the same person. Same for linkedin vanities.
INCOMPATIBLE_IDENTITY_EXISTS = """
EXISTS (
  SELECT 1
  FROM canonical.identity li
  JOIN canonical.identity ri
    ON li.source = ri.source
   AND li.source_id <> ri.source_id
  WHERE li.person_id = mc.left_person_id
    AND ri.person_id = mc.right_person_id
    AND li.source IN ('telegram', 'linkedin')
)
"""

# Boolean expression that's TRUE when a fuzzy_name candidate's name shape
# is too weak to act on. Mirrors SimilarPersons's 2-token rule:
#   - "Dan" vs "Daniel Adams"            → too generic (one-token side)
#   - "Daniel Slupskiy" vs "Daniel Haudenschild"  → both 2 tokens, but
#                                                    surnames don't match
#                                                    or prefix-match
# Other candidate sources (llm_email, phone_match) bridge by identity, not
# name — they get a free pass via the `mc.source = 'fuzzy_name'` guard.
# Requires `lp` and `rp` aliased to canonical.person of the left/right
# persons in the surrounding query.
WEAK_FUZZY_NAME_SHAPE = """
(
  mc.source = 'fuzzy_name'
  AND (
    split_part(lp.display_name, ' ', 2) = ''
    OR split_part(rp.display_name, ' ', 2) = ''
    OR NOT (
      lower(split_part(lp.display_name, ' ', 2)) = lower(split_part(rp.display_name, ' ', 2))
      OR lower(split_part(lp.display_name, ' ', 2)) LIKE lower(rtrim(split_part(rp.display_name, ' ', 2), '.')) || '%'
      OR lower(split_part(rp.display_name, ' ', 2)) LIKE lower(rtrim(split_part(lp.display_name, ' ', 2), '.')) || '%'
    )
  )
)
"""


# --- persons -----------------------------------------------------------

# Person free-text search ($1): matches the name, notes, any identity value
# (email, LinkedIn vanity, phone, x/github/website handles, telegram numeric
# id), and the Telegram username/phone/bio from raw.telegram_user. list + count.
_PERSON_SEARCH = """($1::text IS NULL OR $1 = ''
     OR p.display_name ILIKE '%' || $1 || '%'
     OR p.notes ILIKE '%' || $1 || '%'
     OR EXISTS (SELECT 1 FROM canonical.identity si
                 WHERE si.person_id = p.id AND si.source_id ILIKE '%' || $1 || '%')
     OR EXISTS (SELECT 1 FROM canonical.identity ti
                 JOIN raw.telegram_user tu ON tu.source_user_id = ti.source_id::bigint
                WHERE ti.person_id = p.id AND ti.source = 'telegram'
                  AND (tu.username ILIKE '%' || $1 || '%'
                       OR tu.phone ILIKE '%' || $1 || '%'
                       OR tu.about ILIKE '%' || $1 || '%')))"""


LIST_PERSONS_SQL = f"""
WITH base AS (
  SELECT p.id::text AS person_id, p.display_name, p.visibility,
         COALESCE(p.sensitive, false) AS sensitive,
         (SELECT count(*) FROM canonical.interaction WHERE person_id = p.id) AS total_interactions,
         (SELECT max(occurred_at) FROM canonical.interaction WHERE person_id = p.id) AS last_interaction_at
    FROM canonical.person p
   WHERE p.merged_into IS NULL AND p.deleted_at IS NULL
     AND {_PERSON_SEARCH}
     AND ($2::uuid IS NULL OR EXISTS (
           SELECT 1 FROM memory.company_person cp
            WHERE cp.person_id = p.id AND cp.company_id = $2::uuid))/*VIS*/
)
SELECT
  b.*,
  (SELECT u.username
     FROM canonical.identity i
     JOIN raw.telegram_user u ON u.source_user_id = i.source_id::bigint
    WHERE i.source='telegram' AND i.person_id = b.person_id::uuid
    LIMIT 1) AS telegram_username,
  -- the address an invite would actually go to: mirror gcal invite resolution
  -- (email + gmail identities, gmail preferred) so the picker shows the truth.
  (SELECT source_id
     FROM canonical.identity
    WHERE source IN ('email', 'gmail') AND source_id LIKE '%@%'
      AND person_id = b.person_id::uuid
    ORDER BY source DESC, id LIMIT 1) AS email,
  (SELECT source_id
     FROM canonical.identity
    WHERE source='linkedin' AND person_id = b.person_id::uuid
    LIMIT 1) AS linkedin
FROM base b
ORDER BY b.total_interactions DESC NULLS LAST, b.last_interaction_at DESC NULLS LAST
LIMIT $3 OFFSET $4
"""


COUNT_PERSONS_SQL = f"""
SELECT count(*)::bigint AS n
  FROM canonical.person p
 WHERE p.merged_into IS NULL AND p.deleted_at IS NULL
   AND {_PERSON_SEARCH}
   AND ($2::uuid IS NULL OR EXISTS (
         SELECT 1 FROM memory.company_person cp
          WHERE cp.person_id = p.id AND cp.company_id = $2::uuid))/*VIS*/
"""


async def count_persons(pool: asyncpg.Pool, *, q: str | None, company_id: str | None = None,
                        viewer: dict | None = None) -> int:
    """Total live persons matching the optional name-substring + company
    filters (scoped to what `viewer` may see). Powers the 'Showing N–M of TOTAL'
    header on the persons list."""
    args: list = [q, company_id]
    sql = COUNT_PERSONS_SQL.replace("/*VIS*/", visibility_clause("p", viewer, args))
    async with pool.acquire() as conn:
        row = await conn.fetchrow(sql, *args)
    return int(row["n"]) if row else 0


async def list_persons(
    pool: asyncpg.Pool, *, q: str | None, limit: int, offset: int, company_id: str | None = None,
    viewer: dict | None = None,
) -> list[dict]:
    # scope to what the viewer may see (owner/None → all; member → shared + own).
    # the member-id bind lands at $5, after q/company_id/limit/offset.
    args: list = [q, company_id, limit, offset]
    sql = LIST_PERSONS_SQL.replace("/*VIS*/", visibility_clause("p", viewer, args))
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
    return [dict(r) for r in rows]


# --- prospects (reconnect / BD hunt) ----------------------------------------
# Rank contacts worth reaching back out to. Signal-only, deterministic:
#   score = total_interactions × LEAST(days_since_last_contact, 730)
# i.e. how much relationship you built × how long it's gone cold — the age is
# capped at 2y so a big lapsed relationship outranks an ancient tiny one rather
# than age dominating. Filters carve the pool: min history, a dormancy window,
# exclude people already in the deal pipeline, exclude ones you've dismissed
# (family/personal — the ranking can't know those). Optional keyword filter runs
# over the built profile summary + display name.
PROSPECTS_SQL = """
WITH stat AS (
  SELECT person_id, count(*)::bigint AS total, max(occurred_at) AS last_at
    FROM canonical.interaction
   GROUP BY person_id
),
pipe AS (
  SELECT DISTINCT counterparty_id AS pid
    FROM memory.opportunity WHERE counterparty_id IS NOT NULL
)
SELECT p.id::text AS person_id,
       p.display_name,
       (SELECT u.username FROM canonical.identity i
          JOIN raw.telegram_user u ON u.source_user_id = i.source_id::bigint
         WHERE i.source = 'telegram' AND i.person_id = p.id LIMIT 1) AS telegram_username,
       (SELECT source_id FROM canonical.identity
         WHERE source IN ('email', 'gmail') AND source_id LIKE '%@%' AND person_id = p.id
         ORDER BY source DESC, id LIMIT 1) AS email,
       s.total AS total_interactions,
       s.last_at AS last_interaction_at,
       EXTRACT(day FROM now() - s.last_at)::int AS days_since,
       (mp.summary IS NOT NULL) AS has_profile,
       left(mp.summary, 400) AS summary,
       (pipe.pid IS NOT NULL) AS in_pipeline,
       (pd.person_id IS NOT NULL) AS dismissed,
       (s.total * LEAST(EXTRACT(day FROM now() - s.last_at), 730))::bigint AS score
  FROM stat s
  JOIN canonical.person p ON p.id = s.person_id
                          AND p.merged_into IS NULL AND p.deleted_at IS NULL
  LEFT JOIN memory.profile mp ON mp.person_id = p.id
  LEFT JOIN pipe ON pipe.pid = p.id
  LEFT JOIN memory.prospect_dismissed pd ON pd.person_id = p.id
 WHERE s.total >= $1
   AND s.last_at < now() - make_interval(days => $2::int)
   AND ($3::int IS NULL OR s.last_at >= now() - make_interval(days => $3::int))
   AND ($4 OR pd.person_id IS NULL)
   AND ($5 OR pipe.pid IS NULL)
   AND ($6::text IS NULL OR p.display_name ILIKE '%'||$6||'%' OR mp.summary ILIKE '%'||$6||'%')
 ORDER BY score DESC
 LIMIT $7 OFFSET $8
"""


async def list_prospects(
    pool: asyncpg.Pool, *, min_interactions: int = 20, dormant_after_days: int = 90,
    dormant_before_days: int | None = None, include_dismissed: bool = False,
    include_pipeline: bool = False, q: str | None = None,
    limit: int = 50, offset: int = 0,
) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            PROSPECTS_SQL, max(1, min_interactions), max(0, dormant_after_days),
            dormant_before_days, include_dismissed, include_pipeline,
            (q or None), max(1, min(limit, 200)), max(0, offset),
        )
    return [dict(r) for r in rows]


# ICP semantic variant: same reconnect-eligible pool, but ranked by how close a
# contact's profile sits to the ICP query vector (e5-base, encoded by the MCP
# server). Only people WITH a built profile embedding are searchable. The vector
# is passed as a text literal cast to ::vector, so this pool needs no pgvector codec.
PROSPECTS_SEARCH_SQL = """
WITH stat AS (
  SELECT person_id, count(*)::bigint AS total, max(occurred_at) AS last_at
    FROM canonical.interaction
   GROUP BY person_id
),
pipe AS (
  SELECT DISTINCT counterparty_id AS pid
    FROM memory.opportunity WHERE counterparty_id IS NOT NULL
)
SELECT p.id::text AS person_id,
       p.display_name,
       (SELECT u.username FROM canonical.identity i
          JOIN raw.telegram_user u ON u.source_user_id = i.source_id::bigint
         WHERE i.source = 'telegram' AND i.person_id = p.id LIMIT 1) AS telegram_username,
       (SELECT source_id FROM canonical.identity
         WHERE source IN ('email', 'gmail') AND source_id LIKE '%@%' AND person_id = p.id
         ORDER BY source DESC, id LIMIT 1) AS email,
       s.total AS total_interactions,
       s.last_at AS last_interaction_at,
       EXTRACT(day FROM now() - s.last_at)::int AS days_since,
       true AS has_profile,
       left(mp.summary, 400) AS summary,
       (pipe.pid IS NOT NULL) AS in_pipeline,
       (pd.person_id IS NOT NULL) AS dismissed,
       (s.total * LEAST(EXTRACT(day FROM now() - s.last_at), 730))::bigint AS score,
       round((mp.embedding <=> $8::vector)::numeric, 4) AS distance
  FROM stat s
  JOIN canonical.person p ON p.id = s.person_id
                          AND p.merged_into IS NULL AND p.deleted_at IS NULL
  JOIN memory.profile mp ON mp.person_id = p.id AND mp.embedding IS NOT NULL
  LEFT JOIN pipe ON pipe.pid = p.id
  LEFT JOIN memory.prospect_dismissed pd ON pd.person_id = p.id
 WHERE s.total >= $1
   AND s.last_at < now() - make_interval(days => $2::int)
   AND ($3::int IS NULL OR s.last_at >= now() - make_interval(days => $3::int))
   AND ($4 OR pd.person_id IS NULL)
   AND ($5 OR pipe.pid IS NULL)
 ORDER BY mp.embedding <=> $8::vector
 LIMIT $6 OFFSET $7
"""


async def search_prospects(
    pool: asyncpg.Pool, *, qvec: list[float], min_interactions: int = 20,
    dormant_after_days: int = 90, dormant_before_days: int | None = None,
    include_dismissed: bool = False, include_pipeline: bool = False,
    limit: int = 50, offset: int = 0,
) -> list[dict]:
    vec_lit = "[" + ",".join(f"{x:.6f}" for x in qvec) + "]"
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            PROSPECTS_SEARCH_SQL, max(1, min_interactions), max(0, dormant_after_days),
            dormant_before_days, include_dismissed, include_pipeline,
            max(1, min(limit, 200)), max(0, offset), vec_lit,
        )
    return [dict(r) for r in rows]


async def set_prospect_dismissed(pool: asyncpg.Pool, person_id: str, *, dismissed: bool) -> None:
    async with pool.acquire() as conn:
        if dismissed:
            await conn.execute(
                "INSERT INTO memory.prospect_dismissed (person_id) VALUES ($1::uuid) "
                "ON CONFLICT DO NOTHING", person_id)
        else:
            await conn.execute(
                "DELETE FROM memory.prospect_dismissed WHERE person_id = $1::uuid", person_id)


async def set_person_sharing(pool: asyncpg.Pool, person_id: str, *, visibility: str,
                            owner_member_id: str | None) -> bool:
    """Owner action: mark a contact shared/private (and optionally assign its
    owning member). Returns True if the row existed."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE canonical.person SET visibility = $2, owner_member_id = $3::uuid, "
            "updated_at = now() WHERE id = $1::uuid AND deleted_at IS NULL RETURNING id::text",
            person_id, visibility, owner_member_id)
    return row is not None


async def set_person_sensitive(pool: asyncpg.Pool, person_id: str, *, sensitive: bool) -> bool:
    """Sensitivity routing opt-in: a sensitive contact's messages are only
    ever processed by the LOCAL LLM (scanner/profile/draft). Returns True if
    the row existed."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE canonical.person SET sensitive = $2, updated_at = now() "
            "WHERE id = $1::uuid AND deleted_at IS NULL RETURNING id::text",
            person_id, sensitive)
    return row is not None


PERSON_BASE_SQL = """
SELECT p.id::text       AS person_id,
       p.display_name,
       p.notes,
       p.visibility,
       p.sensitive,
       p.owner_member_id::text AS owner_member_id,
       om.display_name   AS owner_member_name,
       u.username        AS telegram_username,
       u.about           AS telegram_bio,
       u.phone           AS phone,
       -- Effective birthday: a hand-entered override wins, then the Telegram
       -- profile birthday, then a Google-contact one.
       COALESCE(p.birthday, u.birthday, gc.birthday)::text AS birthday
  FROM canonical.person p
  LEFT JOIN memory.fin_member om ON om.id = p.owner_member_id
  LEFT JOIN LATERAL (
    SELECT i.source_id FROM canonical.identity i
     WHERE i.person_id = p.id AND i.source='telegram' ORDER BY i.id LIMIT 1
  ) tg ON true
  LEFT JOIN raw.telegram_user u
    ON tg.source_id IS NOT NULL AND u.source_user_id = tg.source_id::bigint
  LEFT JOIN LATERAL (
    -- Prefer the most-recently-ingested google_contact entry that has a birthday
    -- for any email belonging to this person. NULL when no source records one.
    SELECT g.birthday
      FROM raw.google_contact g
      JOIN canonical.identity ei
        ON ei.source='email' AND ei.person_id = p.id
       AND lower(ei.source_id) = ANY(g.emails)
     WHERE g.birthday IS NOT NULL
     ORDER BY g.ingested_at DESC
     LIMIT 1
  ) gc ON true
 WHERE p.id = $1::uuid
"""

PERSON_STATS_SQL = """
SELECT count(*)::bigint                              AS total_interactions,
       count(*) FILTER (WHERE direction='inbound')   AS inbound_count,
       count(*) FILTER (WHERE direction='outbound')  AS outbound_count,
       min(occurred_at)                              AS first_interaction_at,
       max(occurred_at)                              AS last_interaction_at,
       array_agg(DISTINCT channel ORDER BY channel)  AS channels
  FROM canonical.interaction WHERE person_id = $1::uuid
"""

PERSON_PROFILE_SQL = """
SELECT structured, summary FROM memory.profile WHERE person_id = $1::uuid
"""

PERSON_IDENTITIES_SQL = """
SELECT id AS identity_id, source, source_id, evidence, created_at
  FROM canonical.identity
 WHERE person_id = $1::uuid
 ORDER BY source, id
"""

PERSON_SIGNALS_SQL = """
SELECT signal_type, value, confidence, source
  FROM memory.extracted_signal
 WHERE person_id = $1::uuid
 ORDER BY signal_type, value
"""

PERSON_RECENT_MSGS_SQL = """
-- Per-message group context: for telegram messages, follow
-- (raw_source='telegram', raw_id) → raw.telegram_message → allowlist.
-- group_chat_id and group_title are NULL when:
--   - channel != 'telegram'
--   - it's a private (1:1) chat (chat_id matches a User, not in allowlist)
--   - the allowlist row was deleted
-- The UI uses NULL → "1:1 DM"; non-NULL → "via {group_title}".
SELECT i.occurred_at, i.direction, i.channel,
       left(i.body, 280) AS body_excerpt,
       g.chat_id::text   AS group_chat_id,
       g.title           AS group_title
  FROM canonical.interaction i
  LEFT JOIN raw.telegram_message m
    ON i.raw_source = 'telegram' AND m.id = i.raw_id
  LEFT JOIN raw.telegram_group_allowlist g
    ON g.chat_id = m.chat_id
 WHERE i.person_id = $1::uuid
   AND i.body IS NOT NULL AND length(i.body) > 0
 ORDER BY i.occurred_at DESC
 LIMIT 10
"""


PERSON_LINKEDIN_LOCATION_SQL = """
-- Pull location from raw.linkedin_imported_contact for any email belonging
-- to this person. Most-recent ingest wins when multiple records exist.
SELECT g.location
  FROM raw.linkedin_imported_contact g
  JOIN canonical.identity ei
    ON ei.source='email' AND ei.person_id = $1::uuid
   AND lower(ei.source_id) = ANY(SELECT lower(e) FROM unnest(g.emails) e)
 WHERE g.location IS NOT NULL AND g.location <> ''
 ORDER BY g.ingested_at DESC
 LIMIT 1
"""


async def get_person(pool: asyncpg.Pool, person_id: str) -> dict | None:
    async with pool.acquire() as conn:
        base = await conn.fetchrow(PERSON_BASE_SQL, person_id)
        if base is None:
            return None
        stats = await conn.fetchrow(PERSON_STATS_SQL, person_id)
        prof = await conn.fetchrow(PERSON_PROFILE_SQL, person_id)
        idents = await conn.fetch(PERSON_IDENTITIES_SQL, person_id)
        sigs = await conn.fetch(PERSON_SIGNALS_SQL, person_id)
        msgs = await conn.fetch(PERSON_RECENT_MSGS_SQL, person_id)
        li_loc_row = await conn.fetchrow(PERSON_LINKEDIN_LOCATION_SQL, person_id)
    bios = await list_bios(pool, person_id)
    tasks_for = await list_tasks_for_person(pool, person_id)
    opps_for = await list_opps_for_person(pool, person_id)
    companies_for = await companies_for_person(pool, person_id)

    structured = None
    summary = None
    if prof:
        raw = prof["structured"]
        if isinstance(raw, str):
            try: structured = json.loads(raw)
            except Exception: structured = None
        else:
            structured = raw
        summary = prof["summary"]

    parsed_idents = [
        {
            **dict(i),
            "evidence": (json.loads(i["evidence"]) if isinstance(i["evidence"], str) else i["evidence"])
                        if i["evidence"] else None,
        } for i in idents
    ]

    # Derive a tidy LinkedIn payload from the identity row's evidence, plus
    # location from the linkedin_imported_contact join.
    linkedin = None
    for i in parsed_idents:
        if i["source"] == "linkedin":
            ev = i.get("evidence") or {}
            vanity = i["source_id"]
            linkedin = {
                "vanity": vanity,
                "url": f"https://linkedin.com/in/{vanity}",
                "company": ev.get("company"),
                "position": ev.get("position"),
                "connected_on": ev.get("connected_on"),
                "location": (li_loc_row["location"] if li_loc_row else None),
            }
            break

    return {
        **dict(base),
        **dict(stats),
        "channels": list(stats["channels"] or []) if stats else [],
        "structured": structured,
        "summary": summary,
        "linkedin": linkedin,
        "identities": parsed_idents,
        "signals": [dict(s) for s in sigs],
        "bios": bios,
        "tasks": tasks_for,
        "opportunities": opps_for,
        "companies": companies_for,
        "recent_messages": [dict(m) for m in msgs],
    }


# --- bios + name suggestions ------------------------------------------

# Per-source bio-ish text. The UI renders each row labeled with its
# source so the user sees provenance, and the LLM profile builder reads
# the same query to consolidate into the unified summary. Returns
# possibly multiple rows per source (e.g. LinkedIn has both a job-title
# blurb AND, if imported via vCard, a separate title); the route
# de-dupes verbatim duplicates while preserving source diversity.
PERSON_BIOS_SQL = """
WITH telegram_bio AS (
  SELECT 'telegram'::text  AS source,
         'bio'::text       AS kind,
         u.about           AS text,
         u.last_seen       AS fetched_at
  FROM canonical.identity i
  JOIN raw.telegram_user u ON u.source_user_id = i.source_id::bigint
  WHERE i.source = 'telegram' AND i.person_id = $1::uuid
    AND u.about IS NOT NULL AND length(u.about) > 0
  LIMIT 1
),
linkedin_role AS (
  -- The connection export gives us position + company; treat the pair as
  -- a single role blurb. Pulled from identity.evidence (already joined).
  SELECT 'linkedin'::text AS source,
         'role'::text     AS kind,
         trim(BOTH ' · ' FROM
              concat_ws(' · ',
                       NULLIF(i.evidence->>'position', ''),
                       NULLIF(i.evidence->>'company',  ''))) AS text,
         i.created_at AS fetched_at
  FROM canonical.identity i
  WHERE i.source = 'linkedin' AND i.person_id = $1::uuid
    AND (i.evidence->>'position' IS NOT NULL OR i.evidence->>'company' IS NOT NULL)
  LIMIT 1
),
linkedin_imported AS (
  -- raw.linkedin_imported_contact has a job `title` field — different
  -- from `position` above (the imported_contact source is the address
  -- book export, not the connections export). Joined via emails.
  SELECT DISTINCT ON (g.title)
         'linkedin'::text AS source,
         'title'::text    AS kind,
         g.title          AS text,
         g.ingested_at    AS fetched_at
  FROM raw.linkedin_imported_contact g
  JOIN canonical.identity ei
    ON ei.source='email' AND ei.person_id = $1::uuid
   AND lower(ei.source_id) = ANY(SELECT lower(e) FROM unnest(g.emails) e)
  WHERE g.title IS NOT NULL AND length(g.title) > 0
),
google_notes AS (
  SELECT 'google_contacts'::text AS source,
         'notes'::text           AS kind,
         g.notes                 AS text,
         g.ingested_at           AS fetched_at
  FROM raw.google_contact g
  JOIN canonical.identity ei
    ON ei.source='email' AND ei.person_id = $1::uuid
   AND lower(ei.source_id) = ANY(SELECT lower(e) FROM unnest(g.emails) e)
  WHERE g.notes IS NOT NULL AND length(g.notes) > 0
),
google_role AS (
  SELECT 'google_contacts'::text AS source,
         'role'::text            AS kind,
         trim(BOTH ' · ' FROM
              concat_ws(' · ',
                       NULLIF(g.job_title,    ''),
                       NULLIF(g.organization, ''))) AS text,
         g.ingested_at AS fetched_at
  FROM raw.google_contact g
  JOIN canonical.identity ei
    ON ei.source='email' AND ei.person_id = $1::uuid
   AND lower(ei.source_id) = ANY(SELECT lower(e) FROM unnest(g.emails) e)
  WHERE g.job_title IS NOT NULL OR g.organization IS NOT NULL
)
SELECT * FROM telegram_bio
UNION ALL SELECT * FROM linkedin_role
UNION ALL SELECT * FROM linkedin_imported
UNION ALL SELECT * FROM google_notes
UNION ALL SELECT * FROM google_role
"""


# Candidate display names from each source. The route filters out anything
# equal to the current display_name and dedupes case-insensitively, keeping
# the LinkedIn-source variant where there's a tie.
PERSON_NAME_SUGGESTIONS_SQL = """
-- LinkedIn from connections export (joined via vanity stored as
-- canonical.identity.source_id where source='linkedin').
SELECT 'linkedin'::text AS source,
       trim(concat_ws(' ', NULLIF(lc.first_name,''), NULLIF(lc.last_name,''))) AS suggested,
       'connections.csv'::text AS evidence
  FROM canonical.identity li
  JOIN raw.linkedin_connection lc
    ON lc.url = 'https://www.linkedin.com/in/' || li.source_id
    OR lc.url LIKE '%linkedin.com/in/' || li.source_id || '%'
 WHERE li.person_id = $1::uuid AND li.source = 'linkedin'
   AND (lc.first_name IS NOT NULL OR lc.last_name IS NOT NULL)
UNION ALL
-- LinkedIn from imported contacts (vCard export, richer name fields)
SELECT 'linkedin'::text AS source,
       trim(concat_ws(' ',
            NULLIF(g.prefix, ''),
            NULLIF(g.first_name, ''),
            NULLIF(g.middle_name, ''),
            NULLIF(g.last_name, ''),
            NULLIF(g.suffix, ''))) AS suggested,
       'ImportedContacts.csv'::text AS evidence
  FROM raw.linkedin_imported_contact g
  JOIN canonical.identity ei
    ON ei.source='email' AND ei.person_id = $1::uuid
   AND lower(ei.source_id) = ANY(SELECT lower(e) FROM unnest(g.emails) e)
 WHERE (g.first_name IS NOT NULL OR g.last_name IS NOT NULL)
UNION ALL
-- Google Contacts: prefer display_name (FN), fall back to given+family.
SELECT 'google_contacts'::text AS source,
       coalesce(NULLIF(g.display_name, ''),
                trim(concat_ws(' ', NULLIF(g.given_name,''), NULLIF(g.family_name,'')))) AS suggested,
       g.account_email::text AS evidence
  FROM raw.google_contact g
  JOIN canonical.identity ei
    ON ei.source='email' AND ei.person_id = $1::uuid
   AND lower(ei.source_id) = ANY(SELECT lower(e) FROM unnest(g.emails) e)
 WHERE g.display_name IS NOT NULL OR g.given_name IS NOT NULL OR g.family_name IS NOT NULL
UNION ALL
-- Telegram: first+last from the user profile (often missing for accounts
-- that have a username but no first/last set).
SELECT 'telegram'::text AS source,
       trim(concat_ws(' ', NULLIF(u.first_name,''), NULLIF(u.last_name,''))) AS suggested,
       'telegram_id=' || u.source_user_id::text AS evidence
  FROM canonical.identity ti
  JOIN raw.telegram_user u ON u.source_user_id = ti.source_id::bigint
 WHERE ti.person_id = $1::uuid AND ti.source = 'telegram'
   AND (u.first_name IS NOT NULL OR u.last_name IS NOT NULL)
"""


# Priority order for de-duping the suggestion list (lower = preferred).
_SOURCE_PRIORITY = {"linkedin": 0, "google_contacts": 1, "telegram": 2}


def is_synthetic_display_name(name: str) -> bool:
    """Heuristic for 'this looks auto-generated, would benefit from a real
    one'. Conservative — only flags clearly-synthetic patterns so the UI
    doesn't nag about legitimate single-name handles like 'Madonna'."""
    if not name:
        return True
    n = name.strip()
    if n.startswith("Telegram user "):
        return True
    # Email local-part Title-Cased: "Jdoe" or "John.doe"-derived
    if "@" not in n and "." not in n and "_" not in n:
        # Lowercased handle-ish ("jdoe", "ada_lovelace")
        if n == n.lower() and " " not in n and len(n) <= 24:
            return True
    # Looks like an email local-part (contains dot, no spaces)
    if " " not in n and "." in n and len(n) <= 32 and "@" not in n:
        return True
    return False


async def list_name_suggestions(
    pool: asyncpg.Pool, person_id: str, current_display_name: str,
) -> list[dict]:
    """Returns deduped suggestions sorted by source priority. Each entry
    is {source, suggested, evidence}. Suggestions equal (case-insensitive)
    to the current display_name are filtered out."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(PERSON_NAME_SUGGESTIONS_SQL, person_id)

    current_lower = (current_display_name or "").strip().lower()
    # Bucket by lowercased name; keep the highest-priority source.
    by_name: dict[str, dict] = {}
    for r in rows:
        suggested = (r["suggested"] or "").strip()
        if not suggested or suggested.lower() == current_lower:
            continue
        # Skip suggestions that are obviously worse (e.g. all-numeric, len<2)
        if len(suggested) < 2 or suggested.isdigit():
            continue
        key = suggested.lower()
        prev = by_name.get(key)
        new_pri = _SOURCE_PRIORITY.get(r["source"], 99)
        if prev is None or new_pri < _SOURCE_PRIORITY.get(prev["source"], 99):
            by_name[key] = {
                "source": r["source"],
                "suggested": suggested,
                "evidence": r["evidence"],
            }
    return sorted(
        by_name.values(),
        key=lambda x: (_SOURCE_PRIORITY.get(x["source"], 99), x["suggested"].lower()),
    )


async def list_bios(pool: asyncpg.Pool, person_id: str) -> list[dict]:
    """Per-source bio rows. The UI lists them grouped/labeled by source."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(PERSON_BIOS_SQL, person_id)
    # Dedupe identical (source, text) tuples while preserving order.
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for r in rows:
        text = (r["text"] or "").strip()
        if not text:
            continue
        key = (r["source"], text.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "source": r["source"],
            "kind": r["kind"],
            "text": text,
            "fetched_at": r["fetched_at"],
        })
    return out


# --- soft delete + cleanup queue --------------------------------------

# Detection rule for "automation" persons (newsletter senders, no-reply
# addresses, brand notification names). Two parallel signals:
#   (a) any email identity with a known-automation local-part
#   (b) display_name looks brand-like (ends in .COM/.RU/.IO/etc., or
#       contains a flagged keyword)
# A single COALESCE-driven SELECT keeps it one query.
AUTOMATION_LOCAL_PARTS = (
    'noreply','no-reply','no_reply','donotreply','do-not-reply','do_not_reply',
    'notifications','notification','news','newsletter','newsletters',
    'marketing','info','support','hello','hi','contact','team','alerts',
    'updates','help','admin','postmaster','mailer','mail','bounce',
    'automation','reply','jobs','jobalerts','digest',
)

CLEANUP_CANDIDATES_BASE_SQL = """
WITH automation AS (
  SELECT DISTINCT i.person_id,
         min(i.source_id) FILTER (WHERE i.source='email') AS sample_email
    FROM canonical.identity i
   WHERE i.source = 'email'
     AND lower(split_part(i.source_id, '@', 1)) = ANY($1::text[])
   GROUP BY i.person_id
),
brandlike AS (
  SELECT p.id AS person_id
    FROM canonical.person p
   WHERE p.merged_into IS NULL AND p.deleted_at IS NULL
     AND (
       p.display_name ~ '\\.(RU|COM|IO|ORG|NET|APP)$'
       OR lower(p.display_name) ~
          '(newsletter|noreply|no-reply|notifications|marketing|alerts|support team|jobs?\\b|digest)'
     )
),
combined AS (
  SELECT p.id::text AS person_id,
         p.display_name,
         -- prefer the automation-matched email; otherwise any first email
         -- so the row still shows useful context (e.g. brand-named senders
         -- whose addresses aren't in our local-part allowlist)
         COALESCE(
           a.sample_email,
           (SELECT i.source_id FROM canonical.identity i
              WHERE i.person_id = p.id AND i.source = 'email'
              ORDER BY i.id LIMIT 1)
         ) AS sample_email,
         (a.person_id IS NOT NULL)  AS by_email,
         (b.person_id IS NOT NULL)  AS by_name,
         (SELECT count(*) FROM canonical.interaction WHERE person_id = p.id)        AS msgs,
         (SELECT max(occurred_at) FROM canonical.interaction WHERE person_id = p.id) AS last_at
    FROM canonical.person p
    LEFT JOIN automation a ON a.person_id = p.id
    LEFT JOIN brandlike  b ON b.person_id = p.id
   WHERE p.merged_into IS NULL AND p.deleted_at IS NULL
     AND (a.person_id IS NOT NULL OR b.person_id IS NOT NULL)
     AND ($2::text IS NULL OR p.display_name ILIKE '%' || $2 || '%')
)
"""

LIST_CLEANUP_CANDIDATES_SQL = CLEANUP_CANDIDATES_BASE_SQL + """
SELECT * FROM combined
ORDER BY msgs DESC NULLS LAST, display_name
LIMIT $3 OFFSET $4
"""

COUNT_CLEANUP_CANDIDATES_SQL = CLEANUP_CANDIDATES_BASE_SQL + """
SELECT count(*)::bigint AS n FROM combined
"""


async def list_cleanup_candidates(
    pool: asyncpg.Pool, *, q: str | None, limit: int, offset: int,
) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            LIST_CLEANUP_CANDIDATES_SQL,
            list(AUTOMATION_LOCAL_PARTS), q, limit, offset,
        )
    return [dict(r) for r in rows]


async def count_cleanup_candidates(pool: asyncpg.Pool, *, q: str | None) -> int:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            COUNT_CLEANUP_CANDIDATES_SQL,
            list(AUTOMATION_LOCAL_PARTS), q,
        )
    return int(row["n"]) if row else 0


# --- meeting recaps + suggestion inbox (Phase 2) --------------------

RESOLVE_PERSON_SQL = """
-- Best canonical.person for a free-text name from a meeting recap.
-- Exact (case-insensitive) match wins, then prefix, then substring,
-- ranked within each tier by interaction volume (the busy contact
-- with that name is the likely referent). Returns at most one.
SELECT p.id::text AS person_id, p.display_name,
       (SELECT count(*) FROM canonical.interaction WHERE person_id = p.id) AS msgs,
       (lower(p.display_name) = lower($1)) AS exact
  FROM canonical.person p
 WHERE p.merged_into IS NULL AND p.deleted_at IS NULL
   AND length($1) >= 2
   AND (lower(p.display_name) = lower($1)
        OR p.display_name ILIKE $1 || '%'
        OR p.display_name ILIKE '%' || $1 || '%')
 ORDER BY
   (lower(p.display_name) = lower($1)) DESC,
   (p.display_name ILIKE $1 || '%')    DESC,
   msgs DESC
 LIMIT 1
"""


RESOLVE_PERSON_FUZZY_SQL = """
-- Like RESOLVE_PERSON_SQL but with a trigram-similarity fallback tier, so a
-- transcribed/mis-spelled name ("Brian Kong") still finds the real contact
-- ("Brian Kang"). Exact/prefix/substring still win; fuzzy only catches the
-- tail. Returns sim so the caller can flag low-confidence matches.
SELECT p.id::text AS person_id, p.display_name,
       (lower(p.display_name) = lower($1)) AS exact,
       similarity(p.display_name, $1) AS sim,
       (SELECT count(*) FROM canonical.interaction WHERE person_id = p.id) AS msgs
  FROM canonical.person p
 WHERE p.merged_into IS NULL AND p.deleted_at IS NULL
   AND length($1) >= 2
   AND (lower(p.display_name) = lower($1)
        OR p.display_name ILIKE $1 || '%'
        OR p.display_name ILIKE '%' || $1 || '%'
        OR similarity(p.display_name, $1) > 0.35)
 ORDER BY
   (lower(p.display_name) = lower($1))      DESC,
   (p.display_name ILIKE $1 || '%')         DESC,
   (p.display_name ILIKE '%' || $1 || '%')  DESC,
   similarity(p.display_name, $1)           DESC,
   msgs DESC
 LIMIT 1
"""


async def resolve_person_fuzzy(pool: asyncpg.Pool, name: str) -> dict | None:
    """Resolver with a trigram fuzzy fallback (for transcribed/typo'd names).
    Returns {person_id, display_name, exact, sim} or None."""
    name = (name or "").strip()
    if len(name) < 2:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(RESOLVE_PERSON_FUZZY_SQL, name)
    return dict(row) if row else None


async def person_emails(pool: asyncpg.Pool, person_id: str) -> list[str]:
    """Distinct email addresses on file for a person (for calendar invites)."""
    if not person_id:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT source_id AS email
              FROM canonical.identity
             WHERE person_id = $1::uuid AND source = 'email'
               AND source_id IS NOT NULL AND source_id <> ''
             ORDER BY source_id
            """,
            person_id,
        )
    return [r["email"] for r in rows]


async def resolve_person_by_name(pool: asyncpg.Pool, name: str) -> dict | None:
    """Returns {person_id, display_name, exact} or None. `exact` lets the
    caller assign confidence (exact name → high, fuzzy → medium)."""
    name = (name or "").strip()
    if len(name) < 2:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(RESOLVE_PERSON_SQL, name)
    return dict(row) if row else None


async def get_meeting_recap_by_source(pool: asyncpg.Pool, source: str, source_id: str) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id::text, processed_at, summary FROM memory.meeting_recap WHERE source=$1 AND source_id=$2",
            source, source_id,
        )
    return dict(row) if row else None


async def upsert_meeting_recap(
    pool: asyncpg.Pool, *,
    source: str, source_id: str, title: str | None,
    meeting_date, project_id: str | None, summary: str | None,
    recap: str | None, attendees: list,
) -> dict:
    """Idempotent on (source, source_id) — re-ingesting a meeting updates
    the recap + project + processed_at. Returns the row."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO memory.meeting_recap
              (source, source_id, title, meeting_date, project_id,
               summary, recap, attendees, processed_at)
            VALUES ($1, $2, $3, $4, $5::uuid, $6, $7, $8::jsonb, now())
            ON CONFLICT (source, source_id) DO UPDATE SET
              title        = EXCLUDED.title,
              meeting_date = EXCLUDED.meeting_date,
              project_id   = EXCLUDED.project_id,
              summary      = EXCLUDED.summary,
              recap        = EXCLUDED.recap,
              attendees    = EXCLUDED.attendees,
              processed_at = now()
            RETURNING id::text, source, source_id, title, meeting_date,
                      project_id::text AS project_id, recap, ingested_at
            """,
            source, source_id, title, meeting_date, project_id,
            summary, recap, json.dumps(attendees, default=str),
        )
    return dict(row)


async def delete_suggestions_for_source(pool: asyncpg.Pool, source_ref: str) -> int:
    """Clear PENDING suggestions from a prior ingest of the same recap so
    a re-ingest doesn't pile duplicates. Accepted/dismissed rows are kept
    (they're decisions, not noise)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "DELETE FROM memory.suggestion WHERE source_ref = $1 AND status = 'pending' RETURNING id",
            source_ref,
        )
    return len(rows)


# Cross-source dedup backstop (mirrors interaction_scanner's DEDUP_SQL): a new
# suggestion whose title is trigram-similar (>= threshold) to ANY existing
# suggestion (any status — respects dismissals) or live task for the same
# person is a duplicate. Closes the gap where a Granola recap and a Telegram
# thread surface the same deal/task. The scanner already dedups its side
# against Granola; this dedups Granola's side against everything.
SIMILAR_ITEM_SQL = """
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


async def similar_item_exists(
    pool: asyncpg.Pool, person_id: str | None, title: str, threshold: float,
) -> bool:
    """True if a trigram-similar suggestion/task already exists for this person.
    Person-scoped — cross-source dups for one deal share the counterparty.
    Returns False when person_id is None (no anchor to dedup against)."""
    if not person_id or not (title or "").strip():
        return False
    async with pool.acquire() as conn:
        return await conn.fetchrow(SIMILAR_ITEM_SQL, person_id, title, threshold) is not None


async def create_suggestion(pool: asyncpg.Pool, fields: dict) -> str:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO memory.suggestion
              (kind, title, detail, project_id, person_id, person_name_raw,
               suggested_stage, estimated_value, due_date, owner_hint,
               confidence, source_kind, source_ref, context_at)
            VALUES ($1, $2, $3, $4::uuid, $5::uuid, $6,
                    $7, $8, $9, $10,
                    $11, $12, $13, $14)
            RETURNING id::text
            """,
            fields["kind"], fields["title"], fields.get("detail"),
            fields.get("project_id"), fields.get("person_id"),
            fields.get("person_name_raw"),
            fields.get("suggested_stage"), fields.get("estimated_value"),
            fields.get("due_date"), fields.get("owner_hint"),
            fields.get("confidence") or "medium",
            fields.get("source_kind") or "granola",
            fields.get("source_ref"), fields.get("context_at"),
        )
    return row["id"]


SUGGESTION_FIELDS = """
       s.id::text, s.kind, s.status, s.title, s.detail,
       s.project_id::text AS project_id,
       pr.slug  AS project_slug, pr.name AS project_name, pr.color AS project_color,
       s.person_id::text AS person_id, s.person_name_raw,
       cp.display_name AS person_name,
       s.suggested_stage, s.estimated_value, s.due_date, s.owner_hint,
       s.confidence, s.source_kind, s.source_ref,
       s.created_at, s.decided_at, s.context_at,
       s.accepted_entity_kind, s.accepted_entity_id::text AS accepted_entity_id,
       mr.title AS recap_title, mr.meeting_date AS recap_date
"""

SUGGESTION_FROM = """
  FROM memory.suggestion s
  LEFT JOIN memory.project pr ON pr.id = s.project_id
  LEFT JOIN canonical.person cp ON cp.id = s.person_id
  LEFT JOIN memory.meeting_recap mr
    ON s.source_kind IN ('granola') AND mr.id::text = s.source_ref
"""

LIST_SUGGESTIONS_SQL = f"""
SELECT {SUGGESTION_FIELDS}
{SUGGESTION_FROM}
 WHERE ($1::text IS NULL OR s.status = $1)
   AND ($2::text IS NULL OR s.kind   = $2)
 ORDER BY
   CASE s.status WHEN 'pending' THEN 0 ELSE 1 END,
   CASE s.confidence WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
   s.created_at DESC
 LIMIT $3 OFFSET $4
"""

GET_SUGGESTION_SQL = f"""
SELECT {SUGGESTION_FIELDS}
{SUGGESTION_FROM}
 WHERE s.id = $1::uuid
"""


async def list_suggestions(
    pool: asyncpg.Pool, *, status: str | None, kind: str | None,
    limit: int, offset: int,
) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(LIST_SUGGESTIONS_SQL, status, kind, limit, offset)
    return [dict(r) for r in rows]


async def get_suggestion(pool: asyncpg.Pool, suggestion_id: str) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(GET_SUGGESTION_SQL, suggestion_id)
    return dict(row) if row else None


async def count_pending_suggestions(pool: asyncpg.Pool) -> int:
    async with pool.acquire() as conn:
        n = await conn.fetchval("SELECT count(*) FROM memory.suggestion WHERE status = 'pending'")
    return int(n or 0)


OWED_REPLY_COUNT_SQL = """
WITH recent AS (
  SELECT person_id,
         max(occurred_at) FILTER (WHERE direction = 'inbound')  AS last_in,
         max(occurred_at) FILTER (WHERE direction = 'outbound') AS last_out
    FROM canonical.interaction
   WHERE occurred_at > now() - make_interval(hours => $1)
     AND person_id IS NOT NULL AND body IS NOT NULL AND length(body) > 0
   GROUP BY person_id
)
SELECT count(*)
  FROM recent r
  JOIN canonical.person p ON p.id = r.person_id
                          AND p.merged_into IS NULL AND p.deleted_at IS NULL
 WHERE r.last_in IS NOT NULL AND (r.last_out IS NULL OR r.last_out < r.last_in)
"""

# A real meeting/call = an event with at least one OTHER person (>=2
# attendees), not an all-day marker. This drops solo personal blocks (yoga,
# breakfast, school, jogging, pickups) that clutter the calendar but aren't
# meetings.
MEETINGS_BETWEEN_SQL = """
SELECT count(*) FROM (
  SELECT DISTINCT lower(coalesce(summary, '')), start_ts
    FROM raw.gcal_event
   WHERE start_ts >= $1 AND start_ts < $2
     AND coalesce(status, 'confirmed') <> 'cancelled'
     AND coalesce(self_response, 'accepted') <> 'declined'
     AND NOT all_day
     AND jsonb_array_length(coalesce(attendees, '[]'::jsonb)) >= 2
) t
"""


async def today_counts(pool: asyncpg.Pool, *, day_start, day_end, reply_window_hours: int = 48) -> dict:
    """Live counts for the /today pills (NOT the stale plan snapshot):
    total open work, live deals, pending inbox, people owed a reply, and
    meetings scheduled for *today* only."""
    async with pool.acquire() as conn:
        open_tasks = await conn.fetchval(
            "SELECT count(*) FROM memory.task WHERE deleted_at IS NULL "
            "AND status IN ('open','doing') AND parent_task_id IS NULL"
        )
        live_opps = await conn.fetchval(
            "SELECT count(*) FROM memory.opportunity WHERE deleted_at IS NULL "
            "AND stage NOT IN (SELECT key FROM memory.opp_stage WHERE terminal) AND closed_at IS NULL"
        )
        pending = await conn.fetchval("SELECT count(*) FROM memory.suggestion WHERE status = 'pending'")
        owed = await conn.fetchval(OWED_REPLY_COUNT_SQL, reply_window_hours)
        try:
            meetings_today = await conn.fetchval(MEETINGS_BETWEEN_SQL, day_start, day_end)
        except asyncpg.UndefinedTableError:
            meetings_today = 0
    return {
        "open_tasks": int(open_tasks or 0),
        "live_opps": int(live_opps or 0),
        "pending_suggestions": int(pending or 0),
        "owed_reply": int(owed or 0),
        "meetings_today": int(meetings_today or 0),
    }


async def task_stats(pool: asyncpg.Pool, *, since_date, today, week_start) -> dict:
    """Raw inputs for the gamified /today scoreboard: per-day completion counts
    (BKK-local, since since_date) for streak/weekly, the full-history list of
    completion days (for the all-time best streak), this week's completions by
    project (goal rings), live-pipeline by stage, and overdue/due-today
    priorities. The route assembles streak + week from these."""
    async with pool.acquire() as conn:
        completions = await conn.fetch(
            """
            SELECT (completed_at AT TIME ZONE 'Asia/Bangkok')::date AS d, count(*)::int AS n
              FROM memory.task
             WHERE deleted_at IS NULL AND status = 'done' AND completed_at IS NOT NULL
               AND (completed_at AT TIME ZONE 'Asia/Bangkok')::date >= $1
             GROUP BY 1
            """,
            since_date,
        )
        all_days = await conn.fetch(
            """
            SELECT DISTINCT (completed_at AT TIME ZONE 'Asia/Bangkok')::date AS d
              FROM memory.task
             WHERE deleted_at IS NULL AND status = 'done' AND completed_at IS NOT NULL
             ORDER BY 1
            """
        )
        proj_week = await conn.fetch(
            """
            SELECT coalesce(pr.name, 'Unassigned') AS name, count(*)::int AS n
              FROM memory.task t
              LEFT JOIN memory.project pr ON pr.id = t.project_id
             WHERE t.deleted_at IS NULL AND t.status = 'done' AND t.completed_at IS NOT NULL
               AND (t.completed_at AT TIME ZONE 'Asia/Bangkok')::date >= $1
             GROUP BY 1
             ORDER BY 2 DESC, 1
             LIMIT 6
            """,
            week_start,
        )
        pipeline = await conn.fetch(
            """
            SELECT stage::text AS stage, count(*)::int AS n, coalesce(sum(award_usd), 0)::bigint AS usd
              FROM memory.opportunity
             WHERE deleted_at IS NULL AND stage NOT IN (SELECT key FROM memory.opp_stage WHERE terminal) AND closed_at IS NULL
             GROUP BY stage
             ORDER BY (SELECT os.sort FROM memory.opp_stage os WHERE os.key = stage)
            """
        )
        prio = await conn.fetchrow(
            """
            SELECT count(*) FILTER (WHERE due_date < $1) AS overdue,
                   count(*) FILTER (WHERE due_date = $1) AS due_today
              FROM memory.task
             WHERE deleted_at IS NULL AND status IN ('open', 'doing') AND parent_task_id IS NULL
            """,
            today,
        )
    return {
        "completions": [(r["d"], r["n"]) for r in completions],
        "all_days": [r["d"] for r in all_days],
        "projects_week": [(r["name"], r["n"]) for r in proj_week],
        "pipeline": [(r["stage"], r["n"], int(r["usd"])) for r in pipeline],
        "overdue": int(prio["overdue"] or 0),
        "due_today": int(prio["due_today"] or 0),
    }


# --- app settings (memory.app_setting key/value jsonb) -----------------


async def get_setting(pool: asyncpg.Pool, key: str, default=None):
    """Read a jsonb setting; `default` when unset (or before the migration)."""
    try:
        async with pool.acquire() as conn:
            v = await conn.fetchval("SELECT value FROM memory.app_setting WHERE key=$1", key)
    except asyncpg.UndefinedTableError:
        return default
    if v is None:
        return default
    return json.loads(v) if isinstance(v, str) else v


async def set_setting(pool: asyncpg.Pool, key: str, value) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO memory.app_setting (key, value, updated_at)
            VALUES ($1, $2::jsonb, now())
            ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = now()
            """,
            key, json.dumps(value),
        )


# Live agenda for /today — straight from raw.gcal_event (30-min sync), deduped
# across accounts the same way the daily planner does (same meeting invited on
# several accounts → one row, preferring the accepted copy). The window is an
# OVERLAP test so multi-day events still show on days after they start;
# display_ts clamps them to the window start for grouping/sorting.
UPCOMING_EVENTS_SQL = """
SELECT DISTINCT ON (e.start_ts, lower(coalesce(e.summary, '')))
       e.summary, e.location, e.start_ts, e.end_ts, e.all_day,
       e.self_response, e.account_email,
       GREATEST(e.start_ts, $1) AS display_ts,
       jsonb_array_length(coalesce(e.attendees, '[]'::jsonb)) AS attendee_count
  FROM raw.gcal_event e
 WHERE coalesce(e.end_ts, e.start_ts) > $1 AND e.start_ts < $2
   AND coalesce(e.status, 'confirmed') <> 'cancelled'
   AND coalesce(e.self_response, 'accepted') <> 'declined'
 ORDER BY e.start_ts,
          lower(coalesce(e.summary, '')),
          (e.self_response = 'accepted') DESC
"""


async def upcoming_events(pool: asyncpg.Pool, window_start, window_end) -> list[dict]:
    """Live calendar agenda rows in [window_start, window_end), time-ordered.
    Empty list (not an error) when no calendar has synced yet."""
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(UPCOMING_EVENTS_SQL, window_start, window_end)
    except asyncpg.UndefinedTableError:
        return []
    rows = sorted(rows, key=lambda r: (r["display_ts"], not r["all_day"]))
    return [dict(r) for r in rows]


async def dismiss_suggestion(pool: asyncpg.Pool, suggestion_id: str) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE memory.suggestion SET status='dismissed', decided_at=now() WHERE id=$1::uuid AND status='pending' RETURNING id",
            suggestion_id,
        )
    return row is not None


async def reassign_suggestion_person(
    pool: asyncpg.Pool, suggestion_id: str, person_id: str
) -> dict | None:
    """Re-point a pending suggestion at a different canonical person (fixes
    a mis-resolved name). Sets person_name_raw to that person's display
    name and bumps confidence to 'high' (user-confirmed). Returns the
    refreshed row, or None if the suggestion isn't pending / person invalid."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE memory.suggestion s
               SET person_id = p.id,
                   person_name_raw = p.display_name,
                   confidence = 'high'
              FROM canonical.person p
             WHERE s.id = $1::uuid
               AND s.status = 'pending'
               AND p.id = $2::uuid
               AND p.merged_into IS NULL
               AND p.deleted_at IS NULL
            RETURNING s.id
            """,
            suggestion_id, person_id,
        )
    if row is None:
        return None
    return await get_suggestion(pool, suggestion_id)


async def accept_suggestion(pool: asyncpg.Pool, suggestion_id: str) -> dict | None:
    """Materialize a pending suggestion into a real entity:
      task / person_mention → memory.task
      opportunity           → memory.opportunity
    Then flip the suggestion to accepted with a back-link. Returns
    {entity_kind, entity_id} or None if the suggestion isn't pending."""
    s = await get_suggestion(pool, suggestion_id)
    if s is None or s["status"] != "pending":
        return None

    if s["kind"] == "opportunity":
        opp = await create_opportunity(pool, {
            "title": s["title"],
            "description": s.get("detail"),
            "project_id": s.get("project_id"),
            "counterparty_id": s.get("person_id"),
            "stage": s.get("suggested_stage") or "intro",
            "estimated_value": s.get("estimated_value"),
            "source_kind": s.get("source_kind"),
            "source_ref": s.get("source_ref"),
        })
        entity_kind, entity_id = "opportunity", opp["id"]
    else:
        # 'task' and 'person_mention' both become a task. person_mention
        # defaults to a "follow up with X" framing already in the title.
        task = await create_task(pool, {
            "title": s["title"],
            "description": s.get("detail"),
            "project_id": s.get("project_id"),
            "with_person_id": s.get("person_id"),
            "status": "open",
            "due_date": s.get("due_date"),
            "source_kind": s.get("source_kind"),
            "source_ref": s.get("source_ref"),
        })
        entity_kind, entity_id = "task", task["id"]

    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE memory.suggestion
               SET status='accepted', decided_at=now(),
                   accepted_entity_kind=$2, accepted_entity_id=$3::uuid
             WHERE id=$1::uuid
            """,
            suggestion_id, entity_kind, entity_id,
        )
    return {"entity_kind": entity_kind, "entity_id": entity_id}


RECAPS_FOR_PROJECT_SQL = """
SELECT id::text, source, source_id, title, meeting_date, recap, attendees, ingested_at
  FROM memory.meeting_recap
 WHERE project_id = $1::uuid
 ORDER BY meeting_date DESC NULLS LAST
 LIMIT 20
"""


async def list_recaps_for_project(pool: asyncpg.Pool, project_id: str) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(RECAPS_FOR_PROJECT_SQL, project_id)
    out = []
    for r in rows:
        d = dict(r)
        att = d.get("attendees")
        if isinstance(att, str):
            try: d["attendees"] = json.loads(att)
            except Exception: d["attendees"] = []
        out.append(d)
    return out


# --- daily plan (Phase 4) ----------------------------------------------

LATEST_DAILY_PLAN_SQL = """
SELECT id::text, plan_date, narrative, structured, generated_at
  FROM memory.daily_plan
 ORDER BY plan_date DESC, generated_at DESC
 LIMIT 1
"""


async def get_latest_daily_plan(pool: asyncpg.Pool) -> dict | None:
    """The most recent plan (by the day it's for). asyncpg returns jsonb as
    a str in this codec-less pool, so decode `structured` before returning."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(LATEST_DAILY_PLAN_SQL)
    if row is None:
        return None
    d = dict(row)
    st = d.get("structured")
    if isinstance(st, str):
        try:
            d["structured"] = json.loads(st)
        except Exception:
            d["structured"] = {}
    return d


# --- telegram group allowlist ------------------------------------------

# Whitelisted ORDER BY fragments — the public sort= param maps into one
# of these. Pre-validated to keep f-string interpolation safe.
TELEGRAM_GROUPS_SORTS = {
    "members_asc":  "member_count ASC NULLS LAST",
    "members_desc": "member_count DESC NULLS LAST",
    # `recent` ranks by the CHAT's own last-message timestamp, not
    # when our fetcher last touched the row. last_message_at is NULL
    # when we've never observed a message (most channels we haven't
    # interacted with) — those sort to the bottom.
    "recent":       "last_message_at DESC NULLS LAST",
    "title":        "title ASC NULLS LAST",
}
TELEGRAM_GROUPS_SORT_DEFAULT = "members_asc"

def _telegram_groups_where(alias: str = "") -> str:
    """Shared WHERE clause for list + count. `alias` prefixes each column
    (e.g. `g.` when the FROM uses `raw.telegram_group_allowlist g`); empty
    string when the table is unaliased (the count query)."""
    a = f"{alias}." if alias else ""
    return f"""
 WHERE ($1::text   IS NULL OR {a}title ILIKE '%' || $1 || '%')
   AND ($2::text   IS NULL OR {a}kind = $2::text)
   AND ($3::boolean IS NULL OR {a}enabled = $3::boolean)
   AND ($4::int    IS NULL OR {a}member_count >= $4::int)
   AND ($5::int    IS NULL OR {a}member_count <= $5::int)
"""


def _build_list_telegram_groups_sql(sort: str) -> str:
    order = TELEGRAM_GROUPS_SORTS.get(sort, TELEGRAM_GROUPS_SORTS[TELEGRAM_GROUPS_SORT_DEFAULT])
    # `g.title` as final tiebreaker so order is stable across equal sort
    # keys (e.g. many groups with NULL member_count).
    # `msg_count` counts raw.telegram_message rows matching this
    # allowlist entry's signed chat_id (post the 20260528040000
    # migration). The subquery hits the (chat_id, source_message_id)
    # composite UNIQUE as a covering index — cheap.
    return f"""
SELECT g.chat_id, g.title, g.kind, g.member_count, g.enabled,
       g.first_seen_at, g.last_seen_at, g.enabled_at, g.last_message_at,
       coalesce(
         (SELECT count(*) FROM raw.telegram_message m WHERE m.chat_id = g.chat_id),
         0
       )::int AS msg_count
  FROM raw.telegram_group_allowlist g
{_telegram_groups_where("g")}
 ORDER BY {order}, g.title
 LIMIT $6 OFFSET $7
"""


COUNT_TELEGRAM_GROUPS_SQL = f"""
SELECT count(*)::bigint AS n
  FROM raw.telegram_group_allowlist
{_telegram_groups_where()}
"""


async def list_telegram_groups(
    pool: asyncpg.Pool, *,
    q: str | None, kind: str | None, enabled: bool | None,
    min_members: int | None, max_members: int | None,
    sort: str,
    limit: int, offset: int,
) -> list[dict]:
    sql = _build_list_telegram_groups_sql(sort)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            sql, q, kind, enabled, min_members, max_members, limit, offset,
        )
    return [dict(r) for r in rows]


async def count_telegram_groups(
    pool: asyncpg.Pool, *,
    q: str | None, kind: str | None, enabled: bool | None,
    min_members: int | None = None, max_members: int | None = None,
) -> int:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            COUNT_TELEGRAM_GROUPS_SQL, q, kind, enabled, min_members, max_members,
        )
    return int(row["n"]) if row else 0


GROUP_DETAIL_SQL = """
SELECT chat_id, title, kind, member_count, enabled,
       first_seen_at, last_seen_at, enabled_at, last_message_at,
       coalesce(
         (SELECT count(*) FROM raw.telegram_message m WHERE m.chat_id = g.chat_id),
         0
       )::int AS msg_count
  FROM raw.telegram_group_allowlist g
 WHERE chat_id = $1
"""

GROUP_TOP_SENDERS_SQL = """
-- Distinct senders in this chat, joined to their canonical.person so
-- the UI can link each into /persons/{id}. Limited to the top-N by
-- msg count — keeps the response small.
SELECT
  m.sender_id::text                                 AS sender_telegram_id,
  p.id::text                                        AS person_id,
  COALESCE(p.display_name, u.first_name, '?')       AS display_name,
  count(*)                                          AS msg_count,
  max(m.message_date)                               AS latest_at
FROM raw.telegram_message m
LEFT JOIN raw.telegram_user u   ON u.source_user_id = m.sender_id
LEFT JOIN canonical.identity i  ON i.source = 'telegram'
                                AND i.source_id = m.sender_id::text
LEFT JOIN canonical.person p    ON p.id = i.person_id
                                AND p.merged_into IS NULL
                                AND p.deleted_at IS NULL
WHERE m.chat_id = $1 AND m.sender_id IS NOT NULL
GROUP BY m.sender_id, p.id, p.display_name, u.first_name
ORDER BY msg_count DESC
LIMIT $2
"""

GROUP_RECENT_MSGS_SQL = """
-- Reverse-chronological message stream for a single chat, joined to
-- the sender's canonical.person for display.
SELECT
  m.id,
  m.message_date,
  m.kind,
  left(coalesce(m.text, ''), 600) AS body_excerpt,
  m.sender_id::text                AS sender_telegram_id,
  p.id::text                       AS sender_person_id,
  COALESCE(p.display_name, u.first_name, '?') AS sender_display_name
FROM raw.telegram_message m
LEFT JOIN raw.telegram_user u   ON u.source_user_id = m.sender_id
LEFT JOIN canonical.identity i  ON i.source = 'telegram'
                                AND i.source_id = m.sender_id::text
LEFT JOIN canonical.person p    ON p.id = i.person_id
                                AND p.merged_into IS NULL
                                AND p.deleted_at IS NULL
WHERE m.chat_id = $1
ORDER BY m.message_date DESC
LIMIT $2 OFFSET $3
"""


async def get_group_detail(
    pool: asyncpg.Pool, *, chat_id: int, top_senders_n: int = 20,
    recent_limit: int = 50, recent_offset: int = 0,
) -> dict | None:
    """Group page payload: metadata + top senders + paginated recent
    messages from raw.telegram_message. Returns None if the chat_id
    isn't in the allowlist."""
    async with pool.acquire() as conn:
        group = await conn.fetchrow(GROUP_DETAIL_SQL, chat_id)
        if group is None:
            return None
        senders = await conn.fetch(GROUP_TOP_SENDERS_SQL, chat_id, top_senders_n)
        msgs = await conn.fetch(
            GROUP_RECENT_MSGS_SQL, chat_id, recent_limit, recent_offset,
        )
    return {
        "group": dict(group),
        "senders": [dict(s) for s in senders],
        "recent_messages": [dict(m) for m in msgs],
    }


# --- projects / tasks / opportunities (Phase 1 personal-OS) ---------

# Common projection shape — keep in sync with ProjectRow / TaskRow /
# OpportunityRow in models.py. The aggregate counts power the
# /projects list cards (e.g. "4 open · 2 done").

PROJECT_LIST_SQL = """
SELECT p.id::text, p.slug, p.name, p.description, p.status, p.color,
       p.created_at, p.updated_at,
       (SELECT count(*) FROM memory.project_member pm WHERE pm.project_id = p.id) AS member_count,
       (SELECT count(*) FROM memory.task t
          WHERE t.project_id = p.id AND t.deleted_at IS NULL
            AND t.status IN ('open','doing')) AS open_task_count,
       (SELECT count(*) FROM memory.opportunity o
          WHERE o.project_id = p.id AND o.deleted_at IS NULL
            AND o.stage NOT IN (SELECT key FROM memory.opp_stage WHERE terminal)
            AND o.closed_at IS NULL) AS live_opp_count
  FROM memory.project p
 WHERE p.deleted_at IS NULL
   AND ($1::text IS NULL OR p.status = $1)
 ORDER BY
   CASE p.status WHEN 'active' THEN 0 WHEN 'paused' THEN 1 ELSE 2 END,
   p.name
"""


async def list_projects(pool: asyncpg.Pool, *, status: str | None) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(PROJECT_LIST_SQL, status)
    return [dict(r) for r in rows]


# Same shape as PROJECT_LIST_SQL but limited to projects `person_id` is a member
# of — powers the budget role's scoped Projects list.
PROJECT_LIST_FOR_MEMBER_SQL = PROJECT_LIST_SQL.replace(
    "WHERE p.deleted_at IS NULL",
    "WHERE p.deleted_at IS NULL\n"
    "   AND EXISTS (SELECT 1 FROM memory.project_member pm2\n"
    "                WHERE pm2.project_id = p.id AND pm2.person_id = $2::uuid)",
)


async def list_projects_for_member(pool: asyncpg.Pool, person_id: str, *, status: str | None = None) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(PROJECT_LIST_FOR_MEMBER_SQL, status, person_id)
    return [dict(r) for r in rows]


async def is_project_member(pool: asyncpg.Pool, person_id: str | None, project_id: str | None) -> bool:
    """True iff `person_id` is a member of the project identified by slug-or-id.
    Used to gate every budget-role projects/tasks request to her memberships."""
    if not person_id or not project_id:
        return False
    async with pool.acquire() as conn:
        row = await conn.fetchval(
            """
            SELECT 1 FROM memory.project_member pm
              JOIN memory.project p ON p.id = pm.project_id
             WHERE pm.person_id = $1::uuid
               AND (p.slug = $2 OR p.id::text = $2)
               AND p.deleted_at IS NULL
             LIMIT 1
            """,
            person_id, project_id,
        )
    return row is not None


PROJECT_DETAIL_SQL = """
SELECT p.id::text, p.slug, p.name, p.description, p.status, p.color,
       p.created_at, p.updated_at
  FROM memory.project p
 WHERE p.deleted_at IS NULL
   AND (p.slug = $1 OR p.id::text = $1)
"""

PROJECT_MEMBERS_SQL = """
SELECT pm.person_id::text, pm.role, pm.added_at, p.display_name
  FROM memory.project_member pm
  JOIN canonical.person p ON p.id = pm.person_id
                          AND p.merged_into IS NULL
                          AND p.deleted_at IS NULL
 WHERE pm.project_id = $1::uuid
 ORDER BY pm.added_at
"""


async def get_project(pool: asyncpg.Pool, slug_or_id: str) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(PROJECT_DETAIL_SQL, slug_or_id)
        if row is None:
            return None
        members = await conn.fetch(PROJECT_MEMBERS_SQL, row["id"])
    out = dict(row)
    out["members"] = [dict(m) for m in members]
    return out


async def create_project(pool: asyncpg.Pool, *, slug: str, name: str,
                          description: str | None, status: str, color: str | None) -> dict:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO memory.project (slug, name, description, status, color)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id::text, slug, name, description, status, color,
                      created_at, updated_at
            """,
            slug, name, description, status, color,
        )
    return dict(row)


async def get_or_create_project_by_name(pool: asyncpg.Pool, name: str) -> str:
    """Resolve a project id by case-insensitive name, creating it (active, with a
    unique slug) if absent. Used by the bot's mass-task capture to land items in a
    project like 'Shopping List' without the user pre-creating it."""
    name = (name or "Tasks").strip() or "Tasks"
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id::text FROM memory.project WHERE lower(name) = lower($1) "
            "ORDER BY created_at LIMIT 1", name)
        if row:
            return row["id"]
        base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "project"
        slug, n = base, 1
        while await conn.fetchval("SELECT 1 FROM memory.project WHERE slug = $1", slug):
            n += 1
            slug = f"{base}-{n}"
    proj = await create_project(pool, slug=slug, name=name, description=None,
                                status="active", color=None)
    return proj["id"]


async def patch_project(pool: asyncpg.Pool, project_id: str, fields: dict) -> dict | None:
    """Partial update. Only keys present in `fields` get updated; unknown
    keys silently ignored to avoid arbitrary column writes."""
    allowed = {"name", "description", "status", "color", "slug"}
    sets = []
    args: list = []
    i = 1
    for k, v in fields.items():
        if k not in allowed:
            continue
        sets.append(f"{k} = ${i}")
        args.append(v)
        i += 1
    if not sets:
        return await get_project(pool, project_id)
    args.append(project_id)
    sql = f"""
        UPDATE memory.project SET {', '.join(sets)}
         WHERE id = ${i}::uuid AND deleted_at IS NULL
        RETURNING id::text
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(sql, *args)
    if row is None:
        return None
    return await get_project(pool, project_id)


async def soft_delete_project(pool: asyncpg.Pool, project_id: str) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE memory.project SET deleted_at = now() WHERE id = $1::uuid AND deleted_at IS NULL RETURNING id",
            project_id,
        )
    return row is not None


async def add_project_member(pool: asyncpg.Pool, project_id: str, person_id: str, role: str | None) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO memory.project_member (project_id, person_id, role)
            VALUES ($1::uuid, $2::uuid, $3)
            ON CONFLICT (project_id, person_id) DO UPDATE SET role = EXCLUDED.role
            RETURNING project_id
            """,
            project_id, person_id, role,
        )
    return row is not None


async def remove_project_member(pool: asyncpg.Pool, project_id: str, person_id: str) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "DELETE FROM memory.project_member WHERE project_id = $1::uuid AND person_id = $2::uuid RETURNING project_id",
            project_id, person_id,
        )
    return row is not None


# --- tasks ----------------------------------------------------------

TASK_FIELDS = """
       t.id::text, t.title, t.description,
       t.project_id::text  AS project_id,
       p.slug              AS project_slug,
       p.name              AS project_name,
       p.color             AS project_color,
       t.opportunity_id::text AS opportunity_id,
       o.title                AS opportunity_title,
       t.with_person_id::text AS with_person_id,
       wp.display_name        AS with_person_name,
       t.assignee_person_id::text AS assignee_person_id,
       ap.display_name            AS assignee_name,
       t.parent_task_id::text     AS parent_task_id,
       (SELECT count(*) FROM memory.task st
         WHERE st.parent_task_id = t.id AND st.deleted_at IS NULL) AS subtask_total,
       (SELECT count(*) FROM memory.task st
         WHERE st.parent_task_id = t.id AND st.deleted_at IS NULL
           AND st.status = 'done') AS subtask_done,
       (SELECT count(*) FROM memory.task_person tp
         WHERE tp.task_id = t.id) AS people_count,
       t.status, t.due_date, t.due_time, t.duration_min,
       t.source_kind, t.source_ref,
       t.gcal_account, t.gcal_event_id, t.gcal_html_link, t.gcal_calendar_id,
       t.created_at, t.updated_at, t.completed_at
"""

TASK_BASE_FROM = """
  FROM memory.task t
  LEFT JOIN memory.project p ON p.id = t.project_id
  LEFT JOIN memory.opportunity o ON o.id = t.opportunity_id AND o.deleted_at IS NULL
  LEFT JOIN canonical.person wp ON wp.id = t.with_person_id
                                AND wp.merged_into IS NULL
                                AND wp.deleted_at IS NULL
  LEFT JOIN canonical.person ap ON ap.id = t.assignee_person_id
                                AND ap.merged_into IS NULL
                                AND ap.deleted_at IS NULL
 WHERE t.deleted_at IS NULL
"""

# Main list shows top-level tasks only (parent_task_id IS NULL); subtasks
# surface inside their parent's detail. The person filter matches anyone
# in the involved-people set (task_person), not just the legacy primary.
LIST_TASKS_SQL = f"""
SELECT {TASK_FIELDS}
{TASK_BASE_FROM}
   AND t.parent_task_id IS NULL
   AND ($1::uuid IS NULL OR t.project_id = $1::uuid)
   AND ($2::text IS NULL OR t.status     = $2::text)
   AND ($3::uuid IS NULL OR EXISTS (
         SELECT 1 FROM memory.task_person tp
          WHERE tp.task_id = t.id AND tp.person_id = $3::uuid))
   AND ($4::text IS NULL OR t.title ILIKE '%' || $4 || '%')
 ORDER BY
   CASE t.status WHEN 'doing' THEN 0 WHEN 'open' THEN 1 WHEN 'done' THEN 2 ELSE 3 END,
   t.due_date ASC NULLS LAST,
   t.created_at DESC
 LIMIT $5 OFFSET $6
"""

GET_TASK_SQL = f"""
SELECT {TASK_FIELDS}
{TASK_BASE_FROM}
   AND t.id = $1::uuid
"""

SUBTASKS_SQL = f"""
SELECT {TASK_FIELDS}
{TASK_BASE_FROM}
   AND t.parent_task_id = $1::uuid
 ORDER BY
   CASE t.status WHEN 'doing' THEN 0 WHEN 'open' THEN 1 WHEN 'done' THEN 2 ELSE 3 END,
   t.created_at ASC
"""

TASK_PEOPLE_SQL = """
SELECT p.id::text AS person_id, p.display_name, tp.added_at
  FROM memory.task_person tp
  JOIN canonical.person p ON p.id = tp.person_id
                          AND p.merged_into IS NULL
                          AND p.deleted_at IS NULL
 WHERE tp.task_id = $1::uuid
 ORDER BY tp.added_at
"""


async def list_tasks(
    pool: asyncpg.Pool, *,
    project_id: str | None, status: str | None,
    with_person_id: str | None, q: str | None,
    limit: int, offset: int,
) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            LIST_TASKS_SQL, project_id, status, with_person_id, q, limit, offset,
        )
    return [dict(r) for r in rows]


async def get_task(pool: asyncpg.Pool, task_id: str) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(GET_TASK_SQL, task_id)
    return dict(row) if row else None


async def task_people(pool: asyncpg.Pool, task_id: str) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(TASK_PEOPLE_SQL, task_id)
    return [dict(r) for r in rows]


async def get_task_detail(pool: asyncpg.Pool, task_id: str) -> dict | None:
    """Flat task row + involved people + subtasks (for the slide-over)."""
    row = await get_task(pool, task_id)
    if row is None:
        return None
    async with pool.acquire() as conn:
        people = await conn.fetch(TASK_PEOPLE_SQL, task_id)
        subs = await conn.fetch(SUBTASKS_SQL, task_id)
    row["people"] = [dict(r) for r in people]
    row["subtasks"] = [dict(r) for r in subs]
    return row


async def add_task_person(pool: asyncpg.Pool, task_id: str, person_id: str) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO memory.task_person (task_id, person_id)
            VALUES ($1::uuid, $2::uuid)
            ON CONFLICT (task_id, person_id) DO NOTHING
            RETURNING task_id
            """,
            task_id, person_id,
        )
    # Idempotent: treat an existing membership as success too.
    if row is not None:
        return True
    async with pool.acquire() as conn:
        ex = await conn.fetchrow(
            "SELECT 1 FROM memory.task_person WHERE task_id=$1::uuid AND person_id=$2::uuid",
            task_id, person_id,
        )
    return ex is not None


async def remove_task_person(pool: asyncpg.Pool, task_id: str, person_id: str) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "DELETE FROM memory.task_person WHERE task_id=$1::uuid AND person_id=$2::uuid RETURNING task_id",
            task_id, person_id,
        )
    return row is not None


async def create_task(pool: asyncpg.Pool, fields: dict) -> dict:
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                INSERT INTO memory.task
                  (title, description, project_id, opportunity_id, with_person_id,
                   assignee_person_id, parent_task_id, status, due_date, due_time,
                   duration_min, source_kind, source_ref)
                VALUES ($1, $2, $3::uuid, $4::uuid, $5::uuid, $6::uuid, $7::uuid,
                        $8, $9, $10, $11, $12, $13)
                RETURNING id::text
                """,
                fields.get("title"), fields.get("description"),
                fields.get("project_id"), fields.get("opportunity_id"),
                fields.get("with_person_id"),
                fields.get("assignee_person_id"),
                fields.get("parent_task_id"),
                fields.get("status") or "open",
                fields.get("due_date"),
                fields.get("due_time"),
                fields.get("duration_min"),
                fields.get("source_kind") or "manual",
                fields.get("source_ref"),
            )
            task_id = row["id"]
            # Seed involved-people: the legacy primary contact + any explicit
            # person_ids passed in.
            seed = set()
            if fields.get("with_person_id"):
                seed.add(fields["with_person_id"])
            for pid in fields.get("person_ids") or []:
                if pid:
                    seed.add(pid)
            for pid in seed:
                await conn.execute(
                    """
                    INSERT INTO memory.task_person (task_id, person_id)
                    VALUES ($1::uuid, $2::uuid) ON CONFLICT DO NOTHING
                    """,
                    task_id, pid,
                )
    return await get_task(pool, task_id)  # type: ignore


async def patch_task(pool: asyncpg.Pool, task_id: str, fields: dict) -> dict | None:
    allowed = {"title", "description", "project_id", "opportunity_id",
               "with_person_id", "assignee_person_id", "parent_task_id",
               "status", "due_date", "due_time", "duration_min"}
    type_casts = {"project_id": "::uuid", "opportunity_id": "::uuid",
                  "with_person_id": "::uuid", "assignee_person_id": "::uuid",
                  "parent_task_id": "::uuid"}
    sets = []
    args: list = []
    i = 1
    for k, v in fields.items():
        if k not in allowed:
            continue
        cast = type_casts.get(k, "")
        sets.append(f"{k} = ${i}{cast}")
        args.append(v)
        i += 1
    if "status" in fields and fields.get("status") == "done":
        sets.append("completed_at = now()")
    elif "status" in fields and fields.get("status") != "done":
        sets.append("completed_at = NULL")
    if not sets:
        return await get_task(pool, task_id)
    args.append(task_id)
    sql = f"UPDATE memory.task SET {', '.join(sets)} WHERE id = ${i}::uuid AND deleted_at IS NULL RETURNING id"
    async with pool.acquire() as conn:
        row = await conn.fetchrow(sql, *args)
    if row is None:
        return None
    return await get_task(pool, task_id)


async def soft_delete_task(pool: asyncpg.Pool, task_id: str) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE memory.task SET deleted_at = now() WHERE id = $1::uuid AND deleted_at IS NULL RETURNING id",
            task_id,
        )
    return row is not None


async def set_task_calendar(pool: asyncpg.Pool, task_id: str, account: str,
                            event_id: str, html_link: str | None,
                            calendar_id: str | None = None) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE memory.task SET gcal_account=$2, gcal_event_id=$3, gcal_html_link=$4, gcal_calendar_id=$5 WHERE id=$1::uuid",
            task_id, account, event_id, html_link, calendar_id,
        )


async def clear_task_calendar(pool: asyncpg.Pool, task_id: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE memory.task SET gcal_account=NULL, gcal_event_id=NULL, gcal_html_link=NULL, gcal_calendar_id=NULL WHERE id=$1::uuid",
            task_id,
        )


async def list_calendar_accounts(pool: asyncpg.Pool) -> list[dict]:
    """Connected Google accounts that can take task events (active = re-consented
    with the calendar.events scope). Writes go to each account's primary calendar."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT email FROM raw.gmail_account WHERE status = 'active' ORDER BY email"
        )
    return [{"account": r["email"]} for r in rows]


# --- recurring routines ---------------------------------------------

RECURRING_FIELDS = """
SELECT r.id::text, r.title, r.description,
       r.project_id::text AS project_id, p.slug AS project_slug,
       p.name AS project_name, p.color AS project_color,
       r.with_person_id::text AS with_person_id, wp.display_name AS with_person_name,
       r.at_time, r.duration_min, r.freq, r.byweekday, r.anchor_date, r.active,
       r.gcal_account, r.gcal_event_id, r.gcal_html_link, r.gcal_calendar_id,
       r.created_at, r.updated_at,
       COALESCE((
         SELECT jsonb_agg(jsonb_build_object(
                  'person_id',    pp.id::text,
                  'display_name', pp.display_name,
                  -- best email identity (gmail before a generic 'email' source);
                  -- null when the person has none → not invitable.
                  'email', (SELECT ei.source_id FROM canonical.identity ei
                             WHERE ei.person_id = pp.id
                               AND ei.source IN ('email', 'gmail')
                               AND ei.source_id LIKE '%@%'
                             ORDER BY ei.source DESC, ei.id LIMIT 1),
                  'sensitive', COALESCE(pp.sensitive, false)
                ) ORDER BY lower(pp.display_name))
         FROM memory.recurring_task_participant rtp
         JOIN canonical.person pp ON pp.id = rtp.person_id
                                  AND pp.merged_into IS NULL AND pp.deleted_at IS NULL
         WHERE rtp.recurring_task_id = r.id
       ), '[]'::jsonb) AS participants
"""
RECURRING_FROM = """
  FROM memory.recurring_task r
  LEFT JOIN memory.project p ON p.id = r.project_id
  LEFT JOIN canonical.person wp ON wp.id = r.with_person_id
                                AND wp.merged_into IS NULL AND wp.deleted_at IS NULL
 WHERE r.deleted_at IS NULL
"""


def _routine_row(r) -> dict:
    """dict() a recurring_task row and parse its jsonb `participants` (asyncpg
    hands jsonb back as a str with no codec registered)."""
    d = dict(r)
    p = d.get("participants")
    d["participants"] = json.loads(p) if isinstance(p, str) else (p or [])
    return d


async def list_recurring_tasks(pool: asyncpg.Pool, *, project_id: str | None = None) -> list[dict]:
    sql = RECURRING_FIELDS + RECURRING_FROM
    args: list = []
    if project_id:
        sql += " AND r.project_id = $1::uuid"
        args.append(project_id)
    sql += " ORDER BY r.active DESC, lower(r.title)"
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
    return [_routine_row(r) for r in rows]


async def get_recurring_task(pool: asyncpg.Pool, rid: str) -> dict | None:
    sql = RECURRING_FIELDS + RECURRING_FROM + " AND r.id = $1::uuid"
    async with pool.acquire() as conn:
        row = await conn.fetchrow(sql, rid)
    return _routine_row(row) if row else None


async def set_routine_participants(pool: asyncpg.Pool, rid: str, person_ids: list[str]) -> None:
    """Replace a routine's participant set (people to invite). Deduped; invalid
    person ids are dropped by the FK. Empty list clears everyone."""
    ids = list(dict.fromkeys(person_ids or []))
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM memory.recurring_task_participant WHERE recurring_task_id = $1::uuid", rid)
            if ids:
                await conn.executemany(
                    "INSERT INTO memory.recurring_task_participant (recurring_task_id, person_id) "
                    "VALUES ($1::uuid, $2::uuid) ON CONFLICT DO NOTHING",
                    [(rid, pid) for pid in ids])


async def create_recurring_task(pool: asyncpg.Pool, fields: dict) -> dict:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO memory.recurring_task
              (title, description, project_id, with_person_id, at_time, duration_min, freq, byweekday, anchor_date)
            VALUES ($1, $2, $3::uuid, $4::uuid, $5, $6, $7, $8::smallint[], $9)
            RETURNING id::text
            """,
            fields.get("title"), fields.get("description"),
            fields.get("project_id"), fields.get("with_person_id"),
            fields.get("at_time"), fields.get("duration_min"), fields.get("freq"),
            fields.get("byweekday") or [], fields.get("anchor_date"),
        )
    if fields.get("participant_ids") is not None:
        await set_routine_participants(pool, row["id"], fields["participant_ids"])
    return await get_recurring_task(pool, row["id"])  # type: ignore


async def patch_recurring_task(pool: asyncpg.Pool, rid: str, fields: dict) -> dict | None:
    allowed = {"title", "description", "project_id", "with_person_id",
               "at_time", "duration_min", "freq", "byweekday", "anchor_date", "active"}
    casts = {"project_id": "::uuid", "with_person_id": "::uuid", "byweekday": "::smallint[]"}
    # participant_ids isn't a column — apply it separately (None = leave as-is).
    participant_ids = fields.get("participant_ids")
    sets, args, i = [], [], 1
    for k, v in fields.items():
        if k not in allowed:
            continue
        sets.append(f"{k} = ${i}{casts.get(k, '')}")
        args.append(v)
        i += 1
    async with pool.acquire() as conn:
        if sets:
            args.append(rid)
            sql = f"UPDATE memory.recurring_task SET {', '.join(sets)} WHERE id = ${i}::uuid AND deleted_at IS NULL RETURNING id"
            row = await conn.fetchrow(sql, *args)
            if row is None:
                return None
        else:
            exists = await conn.fetchval(
                "SELECT 1 FROM memory.recurring_task WHERE id = $1::uuid AND deleted_at IS NULL", rid)
            if not exists and participant_ids is None:
                return await get_recurring_task(pool, rid)
            if not exists:
                return None
    if participant_ids is not None:
        await set_routine_participants(pool, rid, participant_ids)
    return await get_recurring_task(pool, rid)


async def delete_recurring_task(pool: asyncpg.Pool, rid: str) -> bool:
    """Soft-delete the template. Already-generated task instances are left
    alone (they're real tasks now); no new ones will be generated."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE memory.recurring_task SET deleted_at = now() WHERE id = $1::uuid AND deleted_at IS NULL RETURNING id",
            rid,
        )
    return row is not None


GENERATE_ROUTINES_SQL = """
INSERT INTO memory.task
  (title, description, project_id, with_person_id, status, due_date, due_time, source_kind, source_ref)
SELECT r.title, r.description, r.project_id, r.with_person_id, 'open', $1::date, r.at_time, 'recurring', r.id::text
  FROM memory.recurring_task r
 WHERE r.deleted_at IS NULL AND r.active
   AND r.anchor_date <= $1::date
   AND (
        r.freq = 'daily'
     OR (r.freq = 'weekly'  AND (extract(isodow from $1::date)::int - 1) = ANY(r.byweekday))
     OR (r.freq = 'monthly' AND extract(day from $1::date) = extract(day from r.anchor_date))
     OR (r.freq = 'yearly'  AND extract(day from $1::date)   = extract(day from r.anchor_date)
                             AND extract(month from $1::date) = extract(month from r.anchor_date))
   )
   AND NOT EXISTS (
        SELECT 1 FROM memory.task t
         WHERE t.source_kind = 'recurring' AND t.source_ref = r.id::text AND t.due_date = $1::date)
RETURNING id::text
"""


async def set_routine_calendar(pool: asyncpg.Pool, rid: str, account: str,
                               event_id: str, html_link: str | None,
                               calendar_id: str | None = None) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE memory.recurring_task SET gcal_account=$2, gcal_event_id=$3, gcal_html_link=$4, gcal_calendar_id=$5 WHERE id=$1::uuid",
            rid, account, event_id, html_link, calendar_id,
        )


async def clear_routine_calendar(pool: asyncpg.Pool, rid: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE memory.recurring_task SET gcal_account=NULL, gcal_event_id=NULL, gcal_html_link=NULL, gcal_calendar_id=NULL WHERE id=$1::uuid",
            rid,
        )


async def generate_routines_for(pool: asyncpg.Pool, target_date) -> int:
    """Materialize a concrete task for every active routine matching target_date,
    skipping any (template, day) already generated. Idempotent. Returns the
    number created."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(GENERATE_ROUTINES_SQL, target_date)
    return len(rows)


# --- opportunities --------------------------------------------------

OPP_FIELDS = """
       o.id::text, o.title, o.description,
       o.project_id::text     AS project_id,
       p.slug                 AS project_slug,
       p.name                 AS project_name,
       p.color                AS project_color,
       o.counterparty_id::text AS counterparty_id,
       cp.display_name         AS counterparty_name,
       o.company,
       o.company_id::text AS company_id,
       co.name            AS company_name,
       o.responsible_person_id::text AS responsible_person_id,
       rp.display_name               AS responsible_name,
       o.stage, o.estimated_value,
       o.award_usd, o.award_note,
       o.tags,
       o.source_kind, o.source_ref,
       (SELECT count(*) FROM memory.task t
         WHERE t.opportunity_id = o.id AND t.deleted_at IS NULL) AS task_count,
       (SELECT count(*) FROM memory.task t
         WHERE t.opportunity_id = o.id AND t.deleted_at IS NULL
           AND t.status IN ('open','doing')) AS open_task_count,
       o.created_at, o.updated_at, o.closed_at
"""

OPP_BASE_FROM = """
  FROM memory.opportunity o
  LEFT JOIN memory.project p ON p.id = o.project_id
  LEFT JOIN canonical.person cp ON cp.id = o.counterparty_id
                                AND cp.merged_into IS NULL
                                AND cp.deleted_at IS NULL
  LEFT JOIN canonical.person rp ON rp.id = o.responsible_person_id
                                AND rp.merged_into IS NULL
                                AND rp.deleted_at IS NULL
  LEFT JOIN memory.company co ON co.id = o.company_id AND co.deleted_at IS NULL
 WHERE o.deleted_at IS NULL
"""

LIST_OPPS_SQL = f"""
SELECT {OPP_FIELDS}
{OPP_BASE_FROM}
   AND ($1::uuid IS NULL OR o.project_id      = $1::uuid)
   AND ($2::text IS NULL OR o.stage::text     = $2::text)
   AND ($3::uuid IS NULL OR o.counterparty_id = $3::uuid)
   AND ($4::text IS NULL OR o.title ILIKE '%' || $4 || '%')
   -- tags: overlap (deal has ANY of the requested tags). NULL/empty = no filter.
   AND ($7::text[] IS NULL OR cardinality($7::text[]) = 0 OR o.tags && $7::text[])
 ORDER BY
   -- Live stages first (most-advanced negotiation on top), terminal last
   (SELECT os.terminal FROM memory.opp_stage os WHERE os.key = o.stage),
   (SELECT os.sort     FROM memory.opp_stage os WHERE os.key = o.stage) DESC,
   o.updated_at DESC
 LIMIT $5 OFFSET $6
"""

GET_OPP_SQL = f"""
SELECT {OPP_FIELDS}
{OPP_BASE_FROM}
   AND o.id = $1::uuid
"""


async def list_opportunities(
    pool: asyncpg.Pool, *,
    project_id: str | None, stage: str | None, counterparty_id: str | None,
    q: str | None, limit: int, offset: int, tags: list[str] | None = None,
) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            LIST_OPPS_SQL, project_id, stage, counterparty_id, q, limit, offset,
            tags or None,
        )
    return [dict(r) for r in rows]


async def list_opportunity_tags(pool: asyncpg.Pool) -> list[str]:
    """Every tag in use, alphabetical — the filter bar's vocabulary. Derived from
    the data so there's no separate tag table to keep in sync."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT DISTINCT unnest(tags) AS tag FROM memory.opportunity "
            "WHERE deleted_at IS NULL ORDER BY tag")
    return [r["tag"] for r in rows]


async def get_opportunity(pool: asyncpg.Pool, opp_id: str) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(GET_OPP_SQL, opp_id)
    return dict(row) if row else None


async def create_opportunity(pool: asyncpg.Pool, fields: dict) -> dict:
    # Free-text estimated_value (still set by suggestion-accept) doubles as
    # the award_note when no explicit note is given, so accepted deals show
    # their value in the new card.
    award_note = fields.get("award_note") or fields.get("estimated_value")
    stage = fields.get("stage") or "intro"
    async with pool.acquire() as conn:
        # Validate against the config table up front (clear 400 instead of an
        # FK violation). LLM suggestions may carry a since-deleted stage key —
        # fall back to the first live stage.
        known = await conn.fetchval("SELECT 1 FROM memory.opp_stage WHERE key = $1", stage)
        if known is None:
            stage = await conn.fetchval(
                "SELECT key FROM memory.opp_stage WHERE NOT terminal ORDER BY sort, key LIMIT 1")
            if stage is None:
                raise ValueError("no opportunity stages configured")
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                INSERT INTO memory.opportunity
                  (title, description, project_id, counterparty_id, company,
                   company_id, responsible_person_id, stage, estimated_value,
                   award_usd, award_note, source_kind, source_ref, tags)
                VALUES ($1, $2, $3::uuid, $4::uuid, $5, $6::uuid, $7::uuid,
                        $8, $9, $10::numeric, $11, $12, $13, $14::text[])
                RETURNING id::text
                """,
                fields.get("title"), fields.get("description"),
                fields.get("project_id"), fields.get("counterparty_id"),
                fields.get("company"), fields.get("company_id"),
                fields.get("responsible_person_id"),
                stage, fields.get("estimated_value"),
                fields.get("award_usd"), award_note,
                fields.get("source_kind") or "manual",
                fields.get("source_ref"),
                fields.get("tags") or [],
            )
            opp_id = row["id"]
            # Open the timeline with a creation event.
            await conn.execute(
                """
                INSERT INTO memory.opportunity_event
                  (opportunity_id, kind, from_stage, to_stage, note)
                VALUES ($1::uuid, 'stage_change', NULL, $2, 'Opportunity created')
                """,
                opp_id, stage,
            )
    return await get_opportunity(pool, opp_id)  # type: ignore


# --- Telegram bot captures (inline-confirm task/opp/event) ----------------

async def create_bot_capture(
    pool: asyncpg.Pool, *, source: str, raw_text: str, parsed: dict, result_kind: str | None,
) -> dict:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO memory.bot_capture (source, raw_text, parsed, result_kind)
            VALUES ($1, $2, $3::jsonb, $4)
            RETURNING id::text
            """,
            source, raw_text, json.dumps(parsed, default=str), result_kind,
        )
    return await get_bot_capture(pool, row["id"])  # type: ignore


async def get_bot_capture(pool: asyncpg.Pool, capture_id: str) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id::text, source, raw_text, parsed, status, result_kind,
                   result_id::text AS result_id, result_ref,
                   chat_id, reply_message_id, decided_at, created_at
              FROM memory.bot_capture WHERE id = $1::uuid
            """,
            capture_id,
        )
    if not row:
        return None
    d = dict(row)
    # codec-less pool → jsonb comes back as a string
    if isinstance(d.get("parsed"), str):
        d["parsed"] = json.loads(d["parsed"])
    return d


async def update_capture_parsed(
    pool: asyncpg.Pool, capture_id: str, parsed: dict, result_kind: str,
) -> bool:
    async with pool.acquire() as conn:
        res = await conn.execute(
            """
            UPDATE memory.bot_capture
               SET parsed = $2::jsonb, result_kind = $3
             WHERE id = $1::uuid AND status IN ('pending', 'confirmed')
            """,
            capture_id, json.dumps(parsed, default=str), result_kind,
        )
    return res.endswith("1")


async def set_capture_reply(
    pool: asyncpg.Pool, capture_id: str, chat_id: int, reply_message_id: int,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE memory.bot_capture SET chat_id=$2, reply_message_id=$3 WHERE id=$1::uuid",
            capture_id, chat_id, reply_message_id,
        )


async def mark_capture_decided(
    pool: asyncpg.Pool, capture_id: str, *,
    status: str, result_kind: str | None = None,
    result_id: str | None = None, result_ref: str | None = None,
) -> dict | None:
    """Atomically transition a pending capture to confirmed/discarded. The
    `WHERE status='pending'` guard makes double-tap / replay safe — the second
    call returns None instead of creating a second entity."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE memory.bot_capture
               SET status = $2, result_kind = COALESCE($3, result_kind),
                   result_id = $4::uuid, result_ref = $5, decided_at = now()
             WHERE id = $1::uuid AND status = 'pending'
            RETURNING id::text
            """,
            capture_id, status, result_kind, result_id, result_ref,
        )
    return dict(row) if row else None


async def patch_opportunity(pool: asyncpg.Pool, opp_id: str, fields: dict) -> dict | None:
    # NOTE: stage changes should go through change_opportunity_stage (records
    # an event). patch handles the plain fields.
    allowed = {"title", "description", "project_id", "counterparty_id",
               "company", "company_id", "responsible_person_id", "estimated_value",
               "award_usd", "award_note", "tags"}
    type_casts = {"project_id": "::uuid", "counterparty_id": "::uuid", "company_id": "::uuid",
                  "responsible_person_id": "::uuid", "award_usd": "::numeric",
                  "tags": "::text[]"}
    sets = []
    args: list = []
    i = 1
    for k, v in fields.items():
        if k not in allowed:
            continue
        cast = type_casts.get(k, "")
        sets.append(f"{k} = ${i}{cast}")
        args.append(v)
        i += 1
    if not sets:
        return await get_opportunity(pool, opp_id)
    args.append(opp_id)
    sql = f"UPDATE memory.opportunity SET {', '.join(sets)} WHERE id = ${i}::uuid AND deleted_at IS NULL RETURNING id"
    async with pool.acquire() as conn:
        row = await conn.fetchrow(sql, *args)
    if row is None:
        return None
    return await get_opportunity(pool, opp_id)


OPP_EVENTS_SQL = """
SELECT id::text, kind, from_stage, to_stage, next_step, note, created_at
  FROM memory.opportunity_event
 WHERE opportunity_id = $1::uuid
 ORDER BY created_at DESC
"""


async def opportunity_events(pool: asyncpg.Pool, opp_id: str) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(OPP_EVENTS_SQL, opp_id)
    return [dict(r) for r in rows]


OPP_TASKS_SQL = f"""
SELECT {TASK_FIELDS}
{TASK_BASE_FROM}
   AND t.opportunity_id = $1::uuid
   AND t.parent_task_id IS NULL
 ORDER BY
   CASE t.status WHEN 'doing' THEN 0 WHEN 'open' THEN 1 WHEN 'done' THEN 2 ELSE 3 END,
   t.due_date ASC NULLS LAST, t.created_at DESC
"""


async def get_opportunity_detail(pool: asyncpg.Pool, opp_id: str) -> dict | None:
    row = await get_opportunity(pool, opp_id)
    if row is None:
        return None
    row["events"] = await opportunity_events(pool, opp_id)
    async with pool.acquire() as conn:
        tasks = await conn.fetch(OPP_TASKS_SQL, opp_id)
    row["tasks"] = [dict(r) for r in tasks]
    return row


async def change_opportunity_stage(
    pool: asyncpg.Pool, opp_id: str, *, stage: str,
    next_step: str | None, note: str | None,
) -> dict | None:
    """Move the deal to a new stage AND log it on the timeline with the
    next step. No-op event if the stage is unchanged but a next_step/note
    is supplied (still recorded). Terminal stages set closed_at."""
    cur = await get_opportunity(pool, opp_id)
    if cur is None:
        return None
    from_stage = cur["stage"]
    async with pool.acquire() as conn:
        closes = await conn.fetchval("SELECT closes FROM memory.opp_stage WHERE key = $1", stage)
        if closes is None:
            raise ValueError(f"unknown stage: {stage}")
        close_sql = "closed_at = now()" if closes else "closed_at = NULL"
        async with conn.transaction():
            await conn.execute(
                f"""
                UPDATE memory.opportunity
                   SET stage = $2, {close_sql}
                 WHERE id = $1::uuid AND deleted_at IS NULL
                """,
                opp_id, stage,
            )
            await conn.execute(
                """
                INSERT INTO memory.opportunity_event
                  (opportunity_id, kind, from_stage, to_stage, next_step, note)
                VALUES ($1::uuid, 'stage_change', $2, $3, $4, $5)
                """,
                opp_id, from_stage, stage, (next_step or None), (note or None),
            )
    return await get_opportunity_detail(pool, opp_id)


async def add_opportunity_event(
    pool: asyncpg.Pool, opp_id: str, *, next_step: str | None, note: str | None,
) -> dict | None:
    """Append a freeform note / next-step to the timeline without a stage
    change."""
    cur = await get_opportunity(pool, opp_id)
    if cur is None:
        return None
    if not (next_step or note):
        return await get_opportunity_detail(pool, opp_id)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO memory.opportunity_event
              (opportunity_id, kind, next_step, note)
            VALUES ($1::uuid, 'note', $2, $3)
            """,
            opp_id, (next_step or None), (note or None),
        )
    return await get_opportunity_detail(pool, opp_id)


async def soft_delete_opportunity(pool: asyncpg.Pool, opp_id: str) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE memory.opportunity SET deleted_at = now() WHERE id = $1::uuid AND deleted_at IS NULL RETURNING id",
            opp_id,
        )
    return row is not None


FOCUS_TASKS_SQL = """
SELECT t.id::text, t.title, t.status, t.due_date,
       p.slug AS project_slug, p.name AS project_name, p.color AS project_color,
       t.with_person_id::text AS person_id, wp.display_name AS person_name
  FROM memory.task t
  LEFT JOIN memory.project p ON p.id = t.project_id
  LEFT JOIN canonical.person wp ON wp.id = t.with_person_id
                                AND wp.merged_into IS NULL AND wp.deleted_at IS NULL
 WHERE t.deleted_at IS NULL
   AND t.status IN ('open', 'doing')
   AND t.parent_task_id IS NULL
"""

FOCUS_OPPS_SQL = """
SELECT o.id::text, o.title, o.stage::text AS stage, o.updated_at, o.award_usd,
       p.slug AS project_slug, p.name AS project_name, p.color AS project_color,
       o.counterparty_id::text AS person_id, cp.display_name AS person_name
  FROM memory.opportunity o
  LEFT JOIN memory.project p ON p.id = o.project_id
  LEFT JOIN canonical.person cp ON cp.id = o.counterparty_id
                                AND cp.merged_into IS NULL AND cp.deleted_at IS NULL
 WHERE o.deleted_at IS NULL AND o.stage NOT IN (SELECT key FROM memory.opp_stage WHERE terminal) AND o.closed_at IS NULL
"""

_STAGE_SCORE = {"contract": 60, "mou": 50, "conversations": 35, "active": 25, "intro": 15}


def _score_task(r: dict, today) -> tuple[int, str]:
    score, bits = 0, []
    due = r.get("due_date")
    if due is not None:
        days = (due - today).days
        if days < 0:
            score += 100 + min(-days, 30); bits.append(f"overdue {-days}d")
        elif days == 0:
            score += 90; bits.append("due today")
        elif days <= 3:
            score += 70; bits.append(f"due in {days}d")
        elif days <= 7:
            score += 40; bits.append(f"due in {days}d")
        else:
            score += 10; bits.append(f"due in {days}d")
    else:
        score += 5
    if r.get("status") == "doing":
        score += 20; bits.append("in progress")
    if not bits:
        bits.append("open task")
    return score, ", ".join(bits)


def _score_opp(r: dict, now) -> tuple[int, str]:
    score, bits = 0, []
    stage = r.get("stage")
    score += _STAGE_SCORE.get(stage, 10)
    bits.append(f"{stage} stage")
    upd = r.get("updated_at")
    if upd is not None:
        days = (now - upd).days
        if days > 7:
            score += min(days, 21); bits.append(f"{days}d since last touch")
    val = r.get("award_usd")
    if val:
        score += min(int(float(val) / 10000), 20)
    return score, ", ".join(bits)


async def focus_items(pool: asyncpg.Pool, *, limit: int) -> list[dict]:
    """A ranked, explainable list of next actions across open tasks + live
    opportunities. Deterministic score (due dates, stage momentum, staleness)
    — no LLM. Each item carries a human reason."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    today = now.date()
    async with pool.acquire() as conn:
        tasks = await conn.fetch(FOCUS_TASKS_SQL)
        opps = await conn.fetch(FOCUS_OPPS_SQL)
    items: list[dict] = []
    for r in tasks:
        d = dict(r)
        sc, reason = _score_task(d, today)
        items.append({
            "kind": "task", "id": d["id"], "title": d["title"],
            "score": sc, "reason": reason,
            "project_slug": d["project_slug"], "project_name": d["project_name"],
            "project_color": d["project_color"],
            "person_id": d["person_id"], "person_name": d["person_name"],
            "stage": None, "status": d["status"],
            "due_date": d["due_date"].isoformat() if d.get("due_date") else None,
        })
    for r in opps:
        d = dict(r)
        sc, reason = _score_opp(d, now)
        items.append({
            "kind": "opportunity", "id": d["id"], "title": d["title"],
            "score": sc, "reason": reason,
            "project_slug": d["project_slug"], "project_name": d["project_name"],
            "project_color": d["project_color"],
            "person_id": d["person_id"], "person_name": d["person_name"],
            "stage": d["stage"], "status": None, "due_date": None,
        })
    items.sort(key=lambda x: x["score"], reverse=True)
    return items[:limit]


PIPELINE_SQL = """
SELECT o.stage::text AS stage,
       count(*)                         AS count,
       coalesce(sum(o.award_usd), 0)::float8 AS usd
  FROM memory.opportunity o
 WHERE o.deleted_at IS NULL
   AND o.stage NOT IN (SELECT key FROM memory.opp_stage WHERE terminal)
   AND o.closed_at IS NULL
   AND ($1::uuid IS NULL OR o.project_id = $1::uuid)
 GROUP BY o.stage
"""

# --- deal-stage config (memory.opp_stage) ---------------------------------

_STAGE_FIELDS = "key, label, sort, terminal, closes, color"


async def list_stages(pool: asyncpg.Pool) -> list[dict]:
    """All configured deal stages in board order, with live usage counts (any
    non-deleted opportunity referencing the key — drives the delete guard)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT {_STAGE_FIELDS},
                   (SELECT count(*)::int FROM memory.opportunity o
                     WHERE o.stage = s.key AND o.deleted_at IS NULL) AS in_use
              FROM memory.opp_stage s
             ORDER BY sort, key
            """
        )
    return [dict(r) for r in rows]


def _slugify_stage_key(label: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    return key[:40] or "stage"


async def create_stage(
    pool: asyncpg.Pool, *, label: str, color: str, terminal: bool, closes: bool,
) -> dict:
    """Add a stage; key is slugified from the label (suffixed if taken). New
    stages land just before the first terminal stage in sort order."""
    base = _slugify_stage_key(label)
    async with pool.acquire() as conn:
        key, n = base, 2
        while await conn.fetchval("SELECT 1 FROM memory.opp_stage WHERE key = $1", key):
            key, n = f"{base}_{n}", n + 1
        sort = await conn.fetchval(
            "SELECT coalesce(max(sort) FILTER (WHERE NOT terminal), 0) + 1 FROM memory.opp_stage")
        row = await conn.fetchrow(
            f"""
            INSERT INTO memory.opp_stage (key, label, sort, terminal, closes, color)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING {_STAGE_FIELDS}, 0 AS in_use
            """,
            key, label, sort, terminal, closes, color,
        )
    return dict(row)


async def update_stage(
    pool: asyncpg.Pool, key: str, *,
    label: str | None = None, color: str | None = None,
    terminal: bool | None = None, closes: bool | None = None,
) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE memory.opp_stage
               SET label    = coalesce($2, label),
                   color    = coalesce($3, color),
                   terminal = coalesce($4, terminal),
                   closes   = coalesce($5, closes)
             WHERE key = $1
            RETURNING {_STAGE_FIELDS},
                   (SELECT count(*)::int FROM memory.opportunity o
                     WHERE o.stage = memory.opp_stage.key AND o.deleted_at IS NULL) AS in_use
            """,
            key, label, color, terminal, closes,
        )
    return dict(row) if row else None


async def delete_stage(pool: asyncpg.Pool, key: str) -> str | None:
    """Delete an unused stage. Returns None on success, or an error string
    ('in_use' / 'not_found' / 'last_stage')."""
    async with pool.acquire() as conn:
        if not await conn.fetchval("SELECT 1 FROM memory.opp_stage WHERE key = $1", key):
            return "not_found"
        live = await conn.fetchval(
            "SELECT count(*) FROM memory.opp_stage WHERE NOT terminal AND key <> $1", key)
        if not live:
            return "last_stage"
        try:
            await conn.execute("DELETE FROM memory.opp_stage WHERE key = $1", key)
        except asyncpg.ForeignKeyViolationError:
            return "in_use"
    return None


async def reorder_stages(pool: asyncpg.Pool, keys: list[str]) -> list[dict]:
    """Set sort order from the given key list (unlisted stages keep their
    relative order after the listed ones)."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            for i, key in enumerate(keys, start=1):
                await conn.execute(
                    "UPDATE memory.opp_stage SET sort = $2 WHERE key = $1", key, i)
    return await list_stages(pool)


async def pipeline_summary(pool: asyncpg.Pool, *, project_id: str | None) -> dict:
    """Live-deal funnel: per-stage count + summed award_usd, plus totals.
    Terminal stages (lost-like) and closed deals are excluded; column order
    comes from memory.opp_stage.sort."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(PIPELINE_SQL, project_id)
        order = [r["key"] for r in await conn.fetch(
            "SELECT key FROM memory.opp_stage WHERE NOT terminal ORDER BY sort, key")]
    by_stage_map = {r["stage"]: {"stage": r["stage"], "count": int(r["count"]), "usd": float(r["usd"] or 0)} for r in rows}
    by_stage = [by_stage_map.get(s, {"stage": s, "count": 0, "usd": 0.0}) for s in order]
    total_usd = sum(s["usd"] for s in by_stage)
    total_count = sum(s["count"] for s in by_stage)
    return {"by_stage": by_stage, "total_usd": total_usd, "total_count": total_count}


# --- person-card enrichments ---------------------------------------

PERSON_TASKS_SQL = f"""
SELECT {TASK_FIELDS}
{TASK_BASE_FROM}
   AND (t.with_person_id = $1::uuid
        OR EXISTS (SELECT 1 FROM memory.task_person tp
                    WHERE tp.task_id = t.id AND tp.person_id = $1::uuid))
 ORDER BY
   CASE t.status WHEN 'doing' THEN 0 WHEN 'open' THEN 1 ELSE 2 END,
   t.due_date NULLS LAST, t.created_at DESC
 LIMIT 20
"""

PERSON_OPPS_SQL = f"""
SELECT {OPP_FIELDS}
{OPP_BASE_FROM}
   AND o.counterparty_id = $1::uuid
 ORDER BY
   (SELECT os.terminal FROM memory.opp_stage os WHERE os.key = o.stage),
   (SELECT os.sort     FROM memory.opp_stage os WHERE os.key = o.stage) DESC,
   o.updated_at DESC
 LIMIT 20
"""


async def list_tasks_for_person(pool: asyncpg.Pool, person_id: str) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(PERSON_TASKS_SQL, person_id)
    return [dict(r) for r in rows]


async def list_opps_for_person(pool: asyncpg.Pool, person_id: str) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(PERSON_OPPS_SQL, person_id)
    return [dict(r) for r in rows]


# --- draft outreach (Phase 5b) -------------------------------------

RECENT_MSGS_FOR_DRAFT_SQL = """
SELECT direction, body
  FROM canonical.interaction
 WHERE person_id = $1::uuid AND body IS NOT NULL AND length(body) > 0
 ORDER BY occurred_at DESC
 LIMIT $2
"""

DRAFT_FIELDS = """
       d.id::text, d.person_id::text AS person_id, d.channel, d.subject, d.body,
       d.status, d.task_id::text AS task_id, d.opportunity_id::text AS opportunity_id,
       d.model, d.created_at, d.updated_at, d.decided_at
"""


async def recent_messages_for_draft(pool: asyncpg.Pool, person_id: str, limit: int = 12) -> list[dict]:
    """Last N messages with this person, returned chronological (oldest first)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(RECENT_MSGS_FOR_DRAFT_SQL, person_id, limit)
    return list(reversed([dict(r) for r in rows]))


async def get_draft(pool: asyncpg.Pool, draft_id: str) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(f"SELECT {DRAFT_FIELDS} FROM memory.draft d WHERE d.id = $1::uuid", draft_id)
    return dict(row) if row else None


async def person_telegram_id(pool: asyncpg.Pool, person_id: str) -> int | None:
    """The Telegram user id for a person (from their telegram identity), or None.
    Used to enqueue a real send via the live Telethon process."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT source_id FROM canonical.identity
             WHERE person_id = $1::uuid AND source = 'telegram'
               AND source_id ~ '^[0-9]+$'
             ORDER BY id LIMIT 1
            """,
            person_id,
        )
    return int(row["source_id"]) if row else None


async def enqueue_telegram_send(
    pool: asyncpg.Pool, *, person_id: str, draft_id: str | None, tg_user_id: int, body: str,
) -> dict:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO memory.telegram_outbox (person_id, draft_id, tg_user_id, body)
            VALUES ($1::uuid, $2::uuid, $3, $4)
            RETURNING id::text, status
            """,
            person_id, draft_id, tg_user_id, body,
        )
    return dict(row)


async def create_draft(pool: asyncpg.Pool, fields: dict) -> dict:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO memory.draft
              (person_id, channel, subject, body, task_id, opportunity_id, model)
            VALUES ($1::uuid, $2, $3, $4, $5::uuid, $6::uuid, $7)
            RETURNING id::text
            """,
            fields["person_id"], fields.get("channel") or "telegram",
            fields.get("subject"), fields.get("body") or "",
            fields.get("task_id"), fields.get("opportunity_id"), fields.get("model"),
        )
    return await get_draft(pool, row["id"])  # type: ignore


async def list_drafts_for_person(pool: asyncpg.Pool, person_id: str) -> list[dict]:
    """Drafts for a person, newest first. Discarded ones are hidden."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT {DRAFT_FIELDS} FROM memory.draft d "
            "WHERE d.person_id = $1::uuid AND d.status <> 'discarded' "
            "ORDER BY d.created_at DESC",
            person_id,
        )
    return [dict(r) for r in rows]


async def patch_draft(pool: asyncpg.Pool, draft_id: str, fields: dict) -> dict | None:
    allowed = {"body", "subject", "status"}
    sets, args, i = [], [], 1
    for k, v in fields.items():
        if k not in allowed:
            continue
        sets.append(f"{k} = ${i}")
        args.append(v)
        i += 1
    if "status" in fields and fields.get("status") in ("sent", "discarded"):
        sets.append("decided_at = now()")
    if not sets:
        return await get_draft(pool, draft_id)
    args.append(draft_id)
    sql = f"UPDATE memory.draft SET {', '.join(sets)} WHERE id = ${i}::uuid RETURNING id"
    async with pool.acquire() as conn:
        row = await conn.fetchrow(sql, *args)
    if row is None:
        return None
    return await get_draft(pool, draft_id)


async def toggle_telegram_group(pool: asyncpg.Pool, chat_id: int, enabled: bool) -> bool:
    """Flip the enabled flag. Records `enabled_at` for audit. Returns
    True if the row existed and was updated."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE raw.telegram_group_allowlist
               SET enabled = $2,
                   enabled_at = CASE WHEN $2 THEN now() ELSE enabled_at END
             WHERE chat_id = $1
            RETURNING chat_id
            """,
            chat_id, enabled,
        )
    return row is not None


# --- group follow suggestions (backlog #3) --------------------------
# Unfollowed, recently-active groups the fetcher has seen messages from, minus
# any the owner dismissed. "Follow" reuses toggle_telegram_group(chat_id, True).

_GROUP_SUGGESTION_WHERE = ("enabled = false AND dismissed_at IS NULL "
                           "AND last_message_at IS NOT NULL")


async def list_group_suggestions(pool: asyncpg.Pool, *, limit: int = 20) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT chat_id::text, title, kind, member_count,
                   last_message_at, last_seen_at
              FROM raw.telegram_group_allowlist
             WHERE {_GROUP_SUGGESTION_WHERE}
             ORDER BY last_message_at DESC NULLS LAST
             LIMIT $1
            """, limit)
    return [dict(r) for r in rows]


async def count_group_suggestions(pool: asyncpg.Pool) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            f"SELECT count(*)::int FROM raw.telegram_group_allowlist WHERE {_GROUP_SUGGESTION_WHERE}") or 0


async def dismiss_group_suggestion(pool: asyncpg.Pool, chat_id: int) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE raw.telegram_group_allowlist SET dismissed_at = now() "
            "WHERE chat_id = $1 RETURNING chat_id", chat_id)
    return row is not None


async def soft_delete_person(pool: asyncpg.Pool, person_id: str) -> bool:
    """Soft-delete: sets deleted_at = now(). Interactions stay attached
    via FK ON DELETE SET NULL anyway, but soft-delete leaves identities/
    profile/photo intact so restore_person() is a clean undo."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE canonical.person
               SET deleted_at = now()
             WHERE id = $1::uuid
               AND merged_into IS NULL
               AND deleted_at IS NULL
            RETURNING id
            """,
            person_id,
        )
    return row is not None


async def soft_delete_persons(pool: asyncpg.Pool, person_ids: list[str]) -> int:
    """Bulk soft-delete. Returns the count actually deleted (rows that
    were already deleted or merged-away don't count)."""
    if not person_ids:
        return 0
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            UPDATE canonical.person
               SET deleted_at = now()
             WHERE id = ANY($1::uuid[])
               AND merged_into IS NULL
               AND deleted_at IS NULL
            RETURNING id
            """,
            person_ids,
        )
    return len(rows)


async def restore_person(pool: asyncpg.Pool, person_id: str) -> bool:
    """Undo a soft-delete."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE canonical.person
               SET deleted_at = NULL
             WHERE id = $1::uuid AND deleted_at IS NOT NULL
            RETURNING id
            """,
            person_id,
        )
    return row is not None


async def rename_person(
    pool: asyncpg.Pool, person_id: str, new_display_name: str,
) -> bool:
    """Updates canonical.person.display_name. Returns True if the row
    existed and was updated. The display_name column is NOT NULL TEXT
    with no length constraint; we cap at 200 to keep things sane."""
    clean = (new_display_name or "").strip()[:200]
    if not clean:
        return False
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE canonical.person
               SET display_name = $2
             WHERE id = $1::uuid AND merged_into IS NULL AND deleted_at IS NULL
            RETURNING id
            """,
            person_id, clean,
        )
    return row is not None


async def set_person_birthday(pool: asyncpg.Pool, person_id: str, birthday) -> bool:
    """Set (or clear, when birthday is None) the manual override birthday on
    canonical.person. This is the one source the normalizer never touches, so a
    hand-entered value survives canonical replay. Returns True if the row exists."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE canonical.person
               SET birthday = $2
             WHERE id = $1::uuid AND merged_into IS NULL AND deleted_at IS NULL
            RETURNING id
            """,
            person_id, birthday,
        )
    return row is not None


# --- identity CRUD -----------------------------------------------------

ADD_IDENTITY_SQL = """
INSERT INTO canonical.identity (person_id, source, source_id, evidence)
VALUES ($1::uuid, $2, $3, $4::jsonb)
ON CONFLICT (source, source_id) DO NOTHING
RETURNING id AS identity_id, source, source_id, evidence, created_at
"""

GET_IDENTITY_SQL = """
SELECT id, person_id::text AS person_id, source, source_id
  FROM canonical.identity
 WHERE id = $1 AND person_id = $2::uuid
"""

DELETE_IDENTITY_SQL = """
DELETE FROM canonical.identity
 WHERE id = $1 AND person_id = $2::uuid
 RETURNING id
"""


async def add_identity(pool: asyncpg.Pool, person_id: str, source: str, source_id: str) -> dict | None:
    evidence = {"added_via": "merge_ui"}
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            ADD_IDENTITY_SQL, person_id, source, source_id.strip(),
            json.dumps(evidence, default=str),
        )
    if row is None:
        return None
    out = dict(row)
    # asyncpg returns jsonb as `str` in this pool (no codec registered);
    # the Pydantic IdentityRow model expects dict | None.
    ev = out.get("evidence")
    if isinstance(ev, str):
        try:
            out["evidence"] = json.loads(ev)
        except json.JSONDecodeError:
            out["evidence"] = None
    return out


async def set_identity_role(
    pool: asyncpg.Pool, person_id: str, identity_id: int,
    *, position: str | None, company: str | None,
) -> dict | None:
    """Record a role/company on an identity you added by hand.

    A LinkedIn vanity typed into the UI is just a link — the position/company
    that enrich a profile only arrive with the connections/contacts CSV import,
    and LinkedIn can't be fetched. This lets you supply them manually, stored in
    the SAME `evidence` shape the importer writes, so everything downstream
    (profile-builder bios, the LinkedIn card, the authoritative-current-title
    prompt rule) treats it identically. Because `evidence` is part of the
    profile input_sig, saving here also marks the summary for rebuild.

    Empty string clears a field. Returns the updated row, or None if the
    identity isn't on this person."""
    patch: dict = {}
    if position is not None:
        patch["position"] = position.strip() or None
    if company is not None:
        patch["company"] = company.strip() or None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE canonical.identity
               SET evidence = coalesce(evidence, '{}'::jsonb) || $3::jsonb
             WHERE id = $2 AND person_id = $1::uuid
             RETURNING id AS identity_id, source, source_id, evidence, created_at
            """,
            person_id, identity_id, json.dumps(patch, default=str),
        )
    if row is None:
        return None
    out = dict(row)
    ev = out.get("evidence")
    if isinstance(ev, str):
        try:
            out["evidence"] = json.loads(ev)
        except json.JSONDecodeError:
            out["evidence"] = None
    return out


async def remove_identity(pool: asyncpg.Pool, person_id: str, identity_id: int) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(DELETE_IDENTITY_SQL, identity_id, person_id)
    return row is not None


# --- photos ------------------------------------------------------------

# Fetches both the explicit photo (if any) and a fallback email — the
# /photo route prefers the photo, but otherwise redirects to Gravatar
# using the email. One round-trip vs two; either field may be NULL.
PERSON_PHOTO_LOOKUP_SQL = """
SELECT
  pp.source                AS photo_source,
  pp.url                   AS photo_url,
  pp.local_path            AS photo_local_path,
  (SELECT i.source_id
     FROM canonical.identity i
    WHERE i.person_id = $1::uuid AND i.source = 'email'
    ORDER BY i.id LIMIT 1) AS first_email
FROM canonical.person p
LEFT JOIN memory.person_photo pp ON pp.person_id = p.id
WHERE p.id = $1::uuid
"""


async def get_person_photo(pool: asyncpg.Pool, person_id: str) -> dict | None:
    """Returns {photo_source, photo_url, photo_local_path, first_email}
    or None if the person doesn't exist. Any of the photo fields may be
    NULL; the caller decides what to serve (file, redirect, gravatar, 404)."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(PERSON_PHOTO_LOOKUP_SQL, person_id)
    return dict(row) if row else None


# --- merge candidates --------------------------------------------------

SIMILAR_PERSONS_SQL = """
-- Other active canonical.persons that look like this one. Two tiers:
--   1. Exact display_name match (always shown — strong signal)
--   2. Same first token + same OR prefix-overlapping second token
--      (catches "Anastasia D." ↔ "Anastasia Drinevskaya" without
--       suggesting every "Alex *" when your own name is Alex)
-- Pairs the user already decided on (approved / rejected / auto_merged)
-- are excluded so dismissed suggestions stay dismissed.
--
-- `identities_preview` is the first 6 (source, source_id) pairs by id,
-- emitted as a JSON array so the UI can render value-bearing chips
-- like "email: brian@factblock.com" instead of just bare source tags.
SELECT p.id::text AS person_id, p.display_name,
       (SELECT count(*) FROM canonical.interaction WHERE person_id = p.id) AS total_interactions,
       (SELECT count(*) FROM canonical.identity    WHERE person_id = p.id) AS identity_count,
       array_agg(DISTINCT i.source ORDER BY i.source) FILTER (WHERE i.source IS NOT NULL) AS sources,
       (SELECT jsonb_agg(jsonb_build_object('source', ii.source, 'source_id', ii.source_id)
                         ORDER BY ii.id)
          FROM (
            SELECT id, source, source_id
              FROM canonical.identity
             WHERE person_id = p.id
             ORDER BY id
             LIMIT 6
          ) ii) AS identities_preview
  FROM canonical.person p
  LEFT JOIN canonical.identity i ON i.person_id = p.id
 WHERE p.merged_into IS NULL AND p.deleted_at IS NULL
   AND p.id <> $1::uuid
   AND (
     p.display_name = $2
     OR (
       -- both names must have at least two tokens
       split_part(p.display_name, ' ', 1) <> ''
       AND split_part(p.display_name, ' ', 2) <> ''
       AND split_part($2, ' ', 1) <> ''
       AND split_part($2, ' ', 2) <> ''
       -- first tokens equal (case-insensitive)
       AND lower(split_part(p.display_name, ' ', 1)) = lower(split_part($2, ' ', 1))
       -- second tokens either equal or one is a prefix of the other
       -- (handles "D." → "Drinevskaya" and similar)
       AND (
         lower(split_part(p.display_name, ' ', 2)) = lower(split_part($2, ' ', 2))
         OR lower(split_part(p.display_name, ' ', 2)) LIKE
            lower(rtrim(split_part($2, ' ', 2), '.')) || '%'
         OR lower(split_part($2, ' ', 2)) LIKE
            lower(rtrim(split_part(p.display_name, ' ', 2), '.')) || '%'
       )
     )
   )
   AND NOT EXISTS (
     SELECT 1 FROM memory.merge_candidate mc
      WHERE mc.status IN ('rejected', 'approved', 'auto_merged')
        AND (
          (mc.left_person_id = $1::uuid AND mc.right_person_id = p.id)
          OR (mc.right_person_id = $1::uuid AND mc.left_person_id = p.id)
        )
   )
 GROUP BY p.id, p.display_name
 ORDER BY (p.display_name = $2) DESC, total_interactions DESC
 LIMIT 10
"""


DISMISS_SIMILAR_SQL = """
-- Insert a "rejected" merge_candidate so the pair stops being proposed.
-- Uses the same expression-unique-index as the generators: pair order is
-- canonicalized by LEAST/GREATEST. ON CONFLICT lets us flip an existing
-- pending row to rejected too.
INSERT INTO memory.merge_candidate
  (left_person_id, right_person_id, source, confidence, score, evidence,
   status, decided_at, decided_by, decision_note)
VALUES
  ($1::uuid, $2::uuid, 'manual_dismiss', 'low', 0,
   $3::jsonb, 'rejected', now(), 'user', 'dismissed from similar-persons UI')
ON CONFLICT (LEAST(left_person_id, right_person_id),
             GREATEST(left_person_id, right_person_id))
DO UPDATE SET
  status = 'rejected', decided_at = now(), decided_by = 'user',
  decision_note = COALESCE(memory.merge_candidate.decision_note || E'\\n','')
                  || 'dismissed from similar-persons UI'
"""


async def dismiss_similar_pair(pool: asyncpg.Pool, a: str, b: str) -> None:
    import json as _json
    a, b = sorted([a, b])
    evidence = {"source": "similar_persons_ui", "reason": "user_dismissed"}
    async with pool.acquire() as conn:
        await conn.execute(DISMISS_SIMILAR_SQL, a, b, _json.dumps(evidence))


async def list_similar_persons(pool: asyncpg.Pool, person_id: str, display_name: str) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(SIMILAR_PERSONS_SQL, person_id, display_name)
    # asyncpg returns jsonb as str in this pool — parse identities_preview
    # so the UI receives a real array of {source, source_id} objects.
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        ip = d.get("identities_preview")
        if isinstance(ip, str):
            try:
                d["identities_preview"] = json.loads(ip)
            except json.JSONDecodeError:
                d["identities_preview"] = []
        elif ip is None:
            d["identities_preview"] = []
        out.append(d)
    return out


PENDING_FOR_PERSON_SQL = f"""
-- Pending merge candidates where this person sits on either side.
-- Joins lp/rp (not just `op`) so the shared INCOMPATIBLE_IDENTITY_EXISTS
-- and WEAK_FUZZY_NAME_SHAPE filters can reference both sides by name.
SELECT mc.id, mc.source, mc.confidence, mc.score, mc.evidence,
       (CASE WHEN mc.left_person_id = $1::uuid
             THEN mc.right_person_id ELSE mc.left_person_id END)::text AS other_person_id,
       (CASE WHEN mc.left_person_id = $1::uuid
             THEN rp.display_name   ELSE lp.display_name   END) AS other_display_name,
       (CASE WHEN mc.left_person_id = $1::uuid
             THEN (SELECT count(*) FROM canonical.identity WHERE person_id = rp.id)
             ELSE (SELECT count(*) FROM canonical.identity WHERE person_id = lp.id)
        END) AS other_identity_count,
       (CASE WHEN mc.left_person_id = $1::uuid
             THEN (SELECT count(*) FROM canonical.interaction WHERE person_id = rp.id)
             ELSE (SELECT count(*) FROM canonical.interaction WHERE person_id = lp.id)
        END) AS other_interactions
  FROM memory.merge_candidate mc
  JOIN canonical.person lp ON lp.id = mc.left_person_id  AND lp.merged_into IS NULL AND lp.deleted_at IS NULL
  JOIN canonical.person rp ON rp.id = mc.right_person_id AND rp.merged_into IS NULL AND rp.deleted_at IS NULL
 WHERE mc.status = 'pending'
   AND (mc.left_person_id = $1::uuid OR mc.right_person_id = $1::uuid)
   AND NOT {INCOMPATIBLE_IDENTITY_EXISTS}
   AND NOT {WEAK_FUZZY_NAME_SHAPE}
 ORDER BY
   CASE mc.confidence WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
   mc.created_at DESC
"""


async def list_pending_for_person(pool: asyncpg.Pool, person_id: str) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(PENDING_FOR_PERSON_SQL, person_id)
    out = []
    for r in rows:
        ev = r["evidence"]
        if isinstance(ev, str):
            try: ev = json.loads(ev)
            except Exception: ev = {}
        out.append({**dict(r), "evidence": ev})
    return out



RELATED_CANDIDATES_SQL = f"""
-- Same filters as LIST_CANDIDATES_SQL.
SELECT mc.id,
       mc.left_person_id::text  AS left_id,
       mc.right_person_id::text AS right_id,
       mc.source, mc.confidence, mc.score, mc.evidence, mc.created_at
  FROM memory.merge_candidate mc
  JOIN canonical.person lp ON lp.id = mc.left_person_id  AND lp.merged_into IS NULL AND lp.deleted_at IS NULL
  JOIN canonical.person rp ON rp.id = mc.right_person_id AND rp.merged_into IS NULL AND rp.deleted_at IS NULL
 WHERE mc.status = 'pending'
   AND mc.id <> $1
   AND (
     mc.left_person_id  = $2::uuid OR mc.left_person_id  = $3::uuid OR
     mc.right_person_id = $2::uuid OR mc.right_person_id = $3::uuid
   )
   AND NOT {INCOMPATIBLE_IDENTITY_EXISTS}
   AND NOT {WEAK_FUZZY_NAME_SHAPE}
 ORDER BY
   CASE mc.confidence WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
   mc.created_at DESC
 LIMIT $4
"""


AUTO_REJECT_ZOMBIE_CANDIDATES_SQL = """
-- Mark every pending merge_candidate as rejected when either side is
-- already merged away. These rows can't be acted on (the loser has no
-- interactions/identities left) and they shouldn't keep occupying queue
-- space. Idempotent: re-running affects only new zombies.
UPDATE memory.merge_candidate mc
   SET status        = 'rejected',
       decided_at    = now(),
       decided_by    = 'system',
       decision_note = COALESCE(decision_note || E'\\n', '') ||
                       'auto-rejected: one side was merged away'
  FROM canonical.person lp, canonical.person rp
 WHERE mc.status = 'pending'
   AND lp.id = mc.left_person_id
   AND rp.id = mc.right_person_id
   AND (lp.merged_into IS NOT NULL OR rp.merged_into IS NOT NULL)
RETURNING mc.id
"""


async def auto_reject_zombie_candidates(pool: asyncpg.Pool) -> int:
    """Sweep stale pending candidates whose left or right person was
    merged away. Returns the count rejected. Cheap; safe to invoke
    on every merge_api startup or periodically."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(AUTO_REJECT_ZOMBIE_CANDIDATES_SQL)
    return len(rows)


AUTO_REJECT_INCOMPATIBLE_CANDIDATES_SQL = f"""
-- Mark every pending merge_candidate as rejected when both sides own
-- different single-account-channel identities (telegram, linkedin).
-- Same person can't own two Telegram accounts (effectively), so
-- @Sockol + @easy2do as left/right is a clear "different people" signal.
-- The decision_note records the reason so we can audit later.
UPDATE memory.merge_candidate mc
   SET status        = 'rejected',
       decided_at    = now(),
       decided_by    = 'system',
       decision_note = COALESCE(decision_note || E'\\n', '') ||
                       'auto-rejected: incompatible single-account identities'
 WHERE mc.status = 'pending'
   AND {INCOMPATIBLE_IDENTITY_EXISTS}
RETURNING mc.id
"""


async def auto_reject_incompatible_candidates(pool: asyncpg.Pool) -> int:
    """Sweep candidates whose two sides own different telegram/linkedin
    accounts (so they can't be the same person). Returns count rejected.
    Cheap; called from merge_api startup alongside the zombie sweep."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(AUTO_REJECT_INCOMPATIBLE_CANDIDATES_SQL)
    return len(rows)


AUTO_REJECT_WEAK_FUZZY_NAME_SQL = f"""
-- Mark every pending fuzzy_name candidate as rejected when the two
-- display_names don't pass the 2-token shape rule (see
-- WEAK_FUZZY_NAME_SHAPE). These are noise like:
--   - Dan vs Daniel Adams  (one-token side, too generic)
--   - Daniel Slupskiy vs Daniel Haudenschild  (different surnames)
UPDATE memory.merge_candidate mc
   SET status        = 'rejected',
       decided_at    = now(),
       decided_by    = 'system',
       decision_note = COALESCE(decision_note || E'\\n', '') ||
                       'auto-rejected: weak fuzzy_name shape (one-token side or mismatched second tokens)'
  FROM canonical.person lp, canonical.person rp
 WHERE mc.status = 'pending'
   AND lp.id = mc.left_person_id
   AND rp.id = mc.right_person_id
   AND {WEAK_FUZZY_NAME_SHAPE}
RETURNING mc.id
"""


async def auto_reject_weak_fuzzy_name_candidates(pool: asyncpg.Pool) -> int:
    """Sweep fuzzy_name pairs that don't satisfy the 2-token shape rule
    (one-token side, or different surnames with no prefix overlap)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(AUTO_REJECT_WEAK_FUZZY_NAME_SQL)
    return len(rows)


async def list_related_candidates(
    pool: asyncpg.Pool, *, candidate_id: int, left_id: str, right_id: str, limit: int,
) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(RELATED_CANDIDATES_SQL, candidate_id, left_id, right_id, limit)
    out = []
    for r in rows:
        ev = r["evidence"]
        if isinstance(ev, str):
            try: ev = json.loads(ev)
            except Exception: ev = {}
        out.append({**dict(r), "evidence": ev})
    return out


LIST_CANDIDATES_SQL = f"""
-- Pending candidates filtered down to those actually worth a human's time:
--   - both sides still live (not merged away tombstones)
--   - no deterministic "different people" signal (different telegram or
--     linkedin source_ids on both sides — INCOMPATIBLE_IDENTITY_EXISTS)
--   - fuzzy_name pairs require a real shared shape (2 tokens both sides
--     with matching/prefix surnames — WEAK_FUZZY_NAME_SHAPE handles this)
-- Each filter also runs as a startup sweep that flips matching pending
-- rows to status='rejected'; the WHERE clause here is belt-and-suspenders
-- so the queue stays clean even mid-window.
SELECT mc.id,
       mc.left_person_id::text  AS left_id,
       mc.right_person_id::text AS right_id,
       mc.source, mc.confidence, mc.score, mc.evidence, mc.created_at
  FROM memory.merge_candidate mc
  JOIN canonical.person lp ON lp.id = mc.left_person_id  AND lp.merged_into IS NULL AND lp.deleted_at IS NULL
  JOIN canonical.person rp ON rp.id = mc.right_person_id AND rp.merged_into IS NULL AND rp.deleted_at IS NULL
 WHERE mc.status = 'pending'
   AND NOT {INCOMPATIBLE_IDENTITY_EXISTS}
   AND NOT {WEAK_FUZZY_NAME_SHAPE}
 ORDER BY
   CASE mc.confidence WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
   mc.created_at DESC
 LIMIT $1 OFFSET $2
"""


async def list_candidates(pool: asyncpg.Pool, *, limit: int, offset: int) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(LIST_CANDIDATES_SQL, limit, offset)
    out = []
    for r in rows:
        ev = r["evidence"]
        if isinstance(ev, str):
            try: ev = json.loads(ev)
            except Exception: ev = {}
        out.append({**dict(r), "evidence": ev})
    return out


GET_CANDIDATE_SQL = """
SELECT id, left_person_id::text AS left_id, right_person_id::text AS right_id,
       source, confidence, score, evidence, status, created_at
  FROM memory.merge_candidate
 WHERE id = $1
"""


async def get_candidate(pool: asyncpg.Pool, candidate_id: int) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(GET_CANDIDATE_SQL, candidate_id)
    if row is None:
        return None
    ev = row["evidence"]
    if isinstance(ev, str):
        try: ev = json.loads(ev)
        except Exception: ev = {}
    return {**dict(row), "evidence": ev}


# --- merge action -------------------------------------------------------

# Every column that references canonical.person(id) must be redirected when two
# people merge — a missed one leaves a dangling reference that renders as a blank
# person link (opportunity counterparties, task/suggestion people, …) because the
# read queries filter `merged_into IS NULL`. Regenerate the authoritative column
# list with (psql):
#   SELECT conrelid::regclass, a.attname
#     FROM pg_constraint c
#     JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
#    WHERE c.contype = 'f' AND c.confrelid = 'canonical.person'::regclass;
# tests/test_merge_coverage.py cross-checks the three lists below against the
# migrations, so a newly-added person FK fails CI until it is classified here.
#
# Each _MERGE_REPOINTS entry is (table, column, dedup_key):
#   dedup_key None  → plain UPDATE (no UNIQUE key covers the person column)
#   dedup_key (...) → the OTHER columns of a UNIQUE key that includes the person
#                     column; loser rows that would collide with an existing
#                     winner row are deleted first so the UPDATE can't trip it
#                     (an empty tuple () means the person column is unique alone).
_MERGE_REPOINTS: list[tuple[str, str, tuple[str, ...] | None]] = [
    ("canonical.interaction",         "person_id",             None),
    ("canonical.identity",            "person_id",             ("source", "source_id")),
    ("memory.extracted_signal",       "person_id",             ("signal_type", "value", "source")),
    ("memory.opportunity",            "counterparty_id",       None),
    ("memory.opportunity",            "responsible_person_id", None),
    ("memory.task",                   "with_person_id",        None),
    ("memory.task",                   "assignee_person_id",    None),
    ("memory.suggestion",             "person_id",             None),
    ("memory.draft",                  "person_id",             None),
    ("memory.telegram_outbox",        "person_id",             None),
    ("memory.recurring_task",         "with_person_id",        None),
    ("memory.fact",                   "person_id",             None),
    ("memory.fin_account",            "person_id",             None),
    ("memory.fin_payee",              "person_id",             None),
    ("memory.fin_transaction",        "person_id",             None),
    ("memory.fin_member",             "person_id",             None),
    ("memory.company_person",         "person_id",             ("company_id",)),
    ("memory.company_link_dismissed", "person_id",             ("company_id",)),
    ("memory.project_member",         "person_id",             ("project_id",)),
    ("memory.task_person",            "person_id",             ("task_id",)),
    ("memory.recurring_task_participant", "person_id",         ("recurring_task_id",)),
    ("memory.interaction_scan_state", "person_id",             ()),
    ("memory.person_photo",           "person_id",             ()),
    ("memory.prospect_dismissed",     "person_id",             ()),
]

# Person FKs whose loser rows are DELETED rather than repointed.
_MERGE_DROP: list[tuple[str, str]] = [
    ("memory.profile", "person_id"),  # winner's narrative supersedes the loser's
]

# Person FKs deliberately left untouched by execute_merge, with the reason:
#   canonical.person.merged_into   → set to the winner in the final mark step
#   memory.merge_candidate.*       → dedup-queue state, already hidden by the
#                                    `merged_into IS NULL` filter and owned by
#                                    decide_candidate; repointing here would forge
#                                    degenerate self-pairs and race the decision.
_MERGE_SKIP: set[tuple[str, str]] = {
    ("canonical.person", "merged_into"),
    ("memory.merge_candidate", "left_person_id"),
    ("memory.merge_candidate", "right_person_id"),
}


def _merge_repoint_sql(table: str, column: str) -> str:
    return f"UPDATE {table} SET {column} = $1::uuid WHERE {column} = $2::uuid"


def _merge_dedup_sql(table: str, column: str, match: tuple[str, ...]) -> str:
    """Delete loser rows that would collide with an existing winner row on the
    UNIQUE key (`column` + `match`), so the follow-up repoint UPDATE is safe."""
    conds = "".join(f"\n           AND l.{m} = w.{m}" for m in match)
    return (
        f"DELETE FROM {table} AS l\n"
        f"     USING {table} AS w\n"
        f"     WHERE l.{column} = $2::uuid\n"
        f"       AND w.{column} = $1::uuid{conds}"
    )


async def execute_merge(pool: asyncpg.Pool, winner_id: str, loser_id: str, note: str) -> None:
    """Redirect every person reference from loser → winner, then mark the loser
    merged. Transactional and idempotent.

    The set of person-referencing columns is enumerated in _MERGE_REPOINTS
    (redirected), _MERGE_DROP (loser rows deleted) and _MERGE_SKIP (handled
    elsewhere / intentionally left alone). test_merge_coverage guards those
    lists against the migrations so a new person FK can't silently start
    leaving dangling references behind again.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            for table, column, dedup in _MERGE_REPOINTS:
                if dedup is not None:
                    await conn.execute(_merge_dedup_sql(table, column, dedup), winner_id, loser_id)
                await conn.execute(_merge_repoint_sql(table, column), winner_id, loser_id)

            for table, column in _MERGE_DROP:
                await conn.execute(f"DELETE FROM {table} WHERE {column} = $1::uuid", loser_id)

            await conn.execute(
                """
                UPDATE canonical.person
                   SET merged_into = $1::uuid, twenty_id = NULL,
                       notes = COALESCE(notes || E'\\n', '') || $2
                 WHERE id = $3::uuid
                """,
                winner_id, note, loser_id,
            )


async def decide_candidate(
    pool: asyncpg.Pool, candidate_id: int, *,
    decision: str, winner_person_id: str | None, loser_person_id: str | None,
    note: str | None,
) -> None:
    """Update the candidate's status. Caller has already executed any merge."""
    async with pool.acquire() as conn:
        status = {
            "approve": "approved",
            "reject": "rejected",
            "defer": "deferred",
        }[decision]
        await conn.execute(
            """
            UPDATE memory.merge_candidate
               SET status = $2,
                   decided_at = now(),
                   decided_by = 'user',
                   decision_note = $3
             WHERE id = $1
            """,
            candidate_id, status, note,
        )


# --- companies (Phase 6) -------------------------------------------------

COMPANY_LIST_SQL = """
SELECT c.id::text, c.name, c.country, c.website, c.domain, c.description, c.visibility,
       c.created_at, c.updated_at,
       (SELECT count(*) FROM memory.company_person cp WHERE cp.company_id = c.id) AS people_count,
       (SELECT count(*) FROM memory.opportunity o
          WHERE o.company_id = c.id AND o.deleted_at IS NULL
            AND o.stage NOT IN (SELECT key FROM memory.opp_stage WHERE terminal) AND o.closed_at IS NULL) AS live_opp_count,
       (SELECT coalesce(sum(o.award_usd), 0)::float8 FROM memory.opportunity o
          WHERE o.company_id = c.id AND o.deleted_at IS NULL
            AND o.stage NOT IN (SELECT key FROM memory.opp_stage WHERE terminal) AND o.closed_at IS NULL) AS pipeline_usd
  FROM memory.company c
 WHERE c.deleted_at IS NULL
   AND ($1::text IS NULL OR c.name ILIKE '%' || $1 || '%')
 ORDER BY people_count DESC, c.name
 LIMIT $2 OFFSET $3
"""

# member-safe company list: NO opportunity/pipeline data (that stays owner-only),
# scoped to shared/own companies via the /*VIS*/ predicate.
COMPANY_LIST_MEMBER_SQL = """
SELECT c.id::text, c.name, c.country, c.website, c.domain, c.description, c.visibility,
       c.created_at, c.updated_at,
       (SELECT count(*) FROM memory.company_person cp WHERE cp.company_id = c.id) AS people_count,
       0 AS live_opp_count, 0::float8 AS pipeline_usd
  FROM memory.company c
 WHERE c.deleted_at IS NULL
   AND ($1::text IS NULL OR c.name ILIKE '%' || $1 || '%')/*VIS*/
 ORDER BY people_count DESC, c.name
 LIMIT $2 OFFSET $3
"""

COMPANY_PEOPLE_SQL = """
SELECT p.id::text AS person_id, p.display_name, cp.role, cp.is_current, cp.added_at
  FROM memory.company_person cp
  JOIN canonical.person p ON p.id = cp.person_id
                          AND p.merged_into IS NULL AND p.deleted_at IS NULL
 WHERE cp.company_id = $1::uuid
 ORDER BY cp.is_current DESC, p.display_name
"""

PERSON_COMPANIES_SQL = """
SELECT c.id::text AS company_id, c.name, cp.role, cp.is_current
  FROM memory.company_person cp
  JOIN memory.company c ON c.id = cp.company_id AND c.deleted_at IS NULL
 WHERE cp.person_id = $1::uuid
 ORDER BY cp.is_current DESC, c.name
"""


def _domain_from_website(website: str | None) -> str | None:
    if not website:
        return None
    w = website.strip().lower()
    w = w.split("://", 1)[-1]           # strip scheme
    w = w.split("/", 1)[0]              # strip path
    w = w.split("?", 1)[0]
    if w.startswith("www."):
        w = w[4:]
    return w or None


async def list_companies(pool: asyncpg.Pool, *, q: str | None, limit: int, offset: int,
                         viewer: dict | None = None) -> list[dict]:
    # a member gets the reduced, scoped list (no opp/pipeline); owner gets all.
    if viewer and not viewer.get("is_owner") and viewer.get("role") != "owner":
        args: list = [q, limit, offset]
        sql = COMPANY_LIST_MEMBER_SQL.replace("/*VIS*/", visibility_clause("c", viewer, args))
    else:
        args = [q, limit, offset]
        sql = COMPANY_LIST_SQL
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
    return [dict(r) for r in rows]


async def get_company(pool: asyncpg.Pool, company_id: str) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT c.id::text, c.name, c.country, c.website, c.domain, c.description,
                   c.visibility, c.owner_member_id::text AS owner_member_id,
                   om.display_name AS owner_member_name,
                   c.created_at, c.updated_at
              FROM memory.company c
              LEFT JOIN memory.fin_member om ON om.id = c.owner_member_id
             WHERE c.id = $1::uuid AND c.deleted_at IS NULL
            """,
            company_id,
        )
    return dict(row) if row else None


async def set_company_sharing(pool: asyncpg.Pool, company_id: str, *, visibility: str,
                             owner_member_id: str | None) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE memory.company SET visibility = $2, owner_member_id = $3::uuid, "
            "updated_at = now() WHERE id = $1::uuid AND deleted_at IS NULL RETURNING id::text",
            company_id, visibility, owner_member_id)
    return row is not None


async def get_company_detail(pool: asyncpg.Pool, company_id: str) -> dict | None:
    row = await get_company(pool, company_id)
    if row is None:
        return None
    async with pool.acquire() as conn:
        people = await conn.fetch(COMPANY_PEOPLE_SQL, company_id)
        opps = await conn.fetch(OPP_FIELDS_QUERY_FOR_COMPANY, company_id)
    row["people"] = [dict(r) for r in people]
    row["opportunities"] = [dict(r) for r in opps]
    return row


async def companies_for_person(pool: asyncpg.Pool, person_id: str) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(PERSON_COMPANIES_SQL, person_id)
    return [dict(r) for r in rows]


async def create_company(pool: asyncpg.Pool, fields: dict) -> dict:
    name = (fields.get("name") or "").strip()
    website = fields.get("website")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO memory.company (name, norm_name, country, website, domain, description)
            VALUES ($1, lower(btrim($1)), $2, $3, $4, $5)
            RETURNING id::text
            """,
            name, fields.get("country"), website,
            _domain_from_website(website), fields.get("description"),
        )
    return await get_company(pool, row["id"])  # type: ignore


async def patch_company(pool: asyncpg.Pool, company_id: str, fields: dict) -> dict | None:
    allowed = {"name", "country", "website", "description"}
    sets, args, i = [], [], 1
    for k, v in fields.items():
        if k not in allowed:
            continue
        sets.append(f"{k} = ${i}")
        args.append(v)
        i += 1
        if k == "name":
            sets.append(f"norm_name = lower(btrim(${i-1}))")
        if k == "website":
            sets.append(f"domain = ${i}")
            args.append(_domain_from_website(v))
            i += 1
    if not sets:
        return await get_company(pool, company_id)
    args.append(company_id)
    sql = f"UPDATE memory.company SET {', '.join(sets)} WHERE id = ${i}::uuid AND deleted_at IS NULL RETURNING id"
    async with pool.acquire() as conn:
        row = await conn.fetchrow(sql, *args)
    if row is None:
        return None
    return await get_company(pool, company_id)


async def soft_delete_company(pool: asyncpg.Pool, company_id: str) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE memory.company SET deleted_at = now() WHERE id = $1::uuid AND deleted_at IS NULL RETURNING id",
            company_id,
        )
    return row is not None


async def add_company_person(pool: asyncpg.Pool, company_id: str, person_id: str, role: str | None, is_current: bool) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO memory.company_person (company_id, person_id, role, is_current)
            VALUES ($1::uuid, $2::uuid, $3, $4)
            ON CONFLICT (company_id, person_id) DO UPDATE SET role = EXCLUDED.role, is_current = EXCLUDED.is_current
            RETURNING company_id
            """,
            company_id, person_id, role, is_current,
        )
    return row is not None


async def remove_company_person(pool: asyncpg.Pool, company_id: str, person_id: str) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "DELETE FROM memory.company_person WHERE company_id=$1::uuid AND person_id=$2::uuid RETURNING company_id",
            company_id, person_id,
        )
    return row is not None


async def merge_companies(pool: asyncpg.Pool, src_id: str, dst_id: str) -> bool:
    """Fold src into dst: move people + opportunities, soft-delete src."""
    if src_id == dst_id:
        return False
    async with pool.acquire() as conn:
        dst = await conn.fetchrow("SELECT id FROM memory.company WHERE id=$1::uuid AND deleted_at IS NULL", dst_id)
        src = await conn.fetchrow("SELECT id FROM memory.company WHERE id=$1::uuid AND deleted_at IS NULL", src_id)
        if dst is None or src is None:
            return False
        async with conn.transaction():
            await conn.execute(
                """
                UPDATE memory.company_person cp SET company_id = $2::uuid
                 WHERE cp.company_id = $1::uuid
                   AND NOT EXISTS (SELECT 1 FROM memory.company_person x
                                    WHERE x.company_id = $2::uuid AND x.person_id = cp.person_id)
                """,
                src_id, dst_id,
            )
            await conn.execute("DELETE FROM memory.company_person WHERE company_id=$1::uuid", src_id)
            await conn.execute("UPDATE memory.opportunity SET company_id=$2::uuid WHERE company_id=$1::uuid", src_id, dst_id)
            await conn.execute("UPDATE memory.company SET deleted_at=now() WHERE id=$1::uuid", src_id)
    return True


# --- LinkedIn link-review queue ------------------------------------------
# Person↔company suggestions from FUZZY LinkedIn-employer matches that the
# exact-norm_name seed missed (e.g. "CoinPost Inc." → entity "CoinPost").
# High-noise, so they're reviewed one-by-one rather than auto-linked. Noise
# tokens (self-employed / freelance / stealth / …) and already-linked or
# previously-dismissed pairs are excluded.
LINK_SUGGESTIONS_SQL = """
WITH pc AS (
  SELECT DISTINCT i.person_id, lower(btrim(lc.company)) AS key,
         btrim(lc.company) AS employer, nullif(btrim(lc.position),'') AS role
    FROM raw.linkedin_connection lc
    JOIN canonical.identity i ON i.source='linkedin'
         AND lower(split_part(rtrim(lc.url,'/'),'/in/',2)) = lower(i.source_id)
   WHERE nullif(btrim(lc.company),'') IS NOT NULL AND nullif(lc.url,'') IS NOT NULL
  UNION
  SELECT DISTINCT i.person_id, lower(btrim(lc.company)),
         btrim(lc.company), nullif(btrim(lc.position),'')
    FROM raw.linkedin_connection lc
    JOIN canonical.identity i ON i.source='email' AND lower(i.source_id)=lower(btrim(lc.email))
   WHERE nullif(btrim(lc.company),'') IS NOT NULL AND nullif(btrim(lc.email),'') IS NOT NULL
),
cand AS (
  SELECT pc.person_id, pc.employer, pc.role, pc.key
    FROM pc
    JOIN canonical.person p ON p.id = pc.person_id
                            AND p.merged_into IS NULL AND p.deleted_at IS NULL
   WHERE length(pc.key) >= 3
     AND pc.key NOT IN (
       'self-employed','self employed','selfemployed','freelance','freelancer',
       'stealth','stealth startup','stealth mode','nda','n/a','na','none',
       'retired','student','unemployed','various','independent','consultant',
       'consulting','crypto','web3','blockchain','private','upwork','fiverr','x')
     AND NOT EXISTS (SELECT 1 FROM memory.company c
                      WHERE c.deleted_at IS NULL AND c.norm_name = pc.key)
)
SELECT c.person_id::text, p.display_name AS person_name, c.employer, c.role,
       m.id::text AS company_id, m.name AS company_name, m.domain AS company_domain,
       round(similarity(m.norm_name, c.key)::numeric, 2)::float8 AS similarity,
       (SELECT i2.source_id FROM canonical.identity i2
         WHERE i2.source='linkedin' AND i2.person_id = c.person_id
         ORDER BY i2.id LIMIT 1) AS linkedin_vanity
  FROM cand c
  JOIN canonical.person p ON p.id = c.person_id
  JOIN LATERAL (
    SELECT id, name, norm_name, domain
      FROM memory.company co
     WHERE co.deleted_at IS NULL AND co.norm_name <> c.key
     ORDER BY similarity(co.norm_name, c.key) DESC
     LIMIT 1
  ) m ON true
 WHERE similarity(m.norm_name, c.key) > 0.55
   AND NOT EXISTS (SELECT 1 FROM memory.company_person x
                    WHERE x.company_id = m.id AND x.person_id = c.person_id)
   AND NOT EXISTS (SELECT 1 FROM memory.company_link_dismissed d
                    WHERE d.person_id = c.person_id AND d.company_id = m.id)
 ORDER BY similarity DESC, person_name
 LIMIT $1
"""


async def list_link_suggestions(pool: asyncpg.Pool, *, limit: int = 100) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(LINK_SUGGESTIONS_SQL, limit)
    return [dict(r) for r in rows]


async def count_link_suggestions(pool: asyncpg.Pool) -> int:
    async with pool.acquire() as conn:
        rows = await conn.fetch(LINK_SUGGESTIONS_SQL, 1000)
    return len(rows)


async def dismiss_link_suggestion(pool: asyncpg.Pool, person_id: str, company_id: str) -> bool:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO memory.company_link_dismissed (person_id, company_id)
            VALUES ($1::uuid, $2::uuid)
            ON CONFLICT (person_id, company_id) DO NOTHING
            """,
            person_id, company_id,
        )
    return True


OPP_FIELDS_QUERY_FOR_COMPANY = f"""
SELECT {OPP_FIELDS}
{OPP_BASE_FROM}
   AND o.company_id = $1::uuid
 ORDER BY
   (SELECT os.terminal FROM memory.opp_stage os WHERE os.key = o.stage),
   (SELECT os.sort     FROM memory.opp_stage os WHERE os.key = o.stage) DESC,
   o.updated_at DESC
"""


# ===================================================================
# Finance / budget module
# ===================================================================

# --- assets ---------------------------------------------------------

FIN_ASSET_FIELDS = """
       a.id::text, a.code, a.name, a.kind, a.decimals, a.symbol,
       a.chain, a.contract_address, a.is_active,
       (SELECT fr.rate::float8 FROM memory.fin_fx_rate fr
         WHERE fr.asset_id = a.id AND fr.quote = 'USD'
         ORDER BY fr.rate_date DESC LIMIT 1) AS usd_rate
"""


async def list_fin_assets(pool: asyncpg.Pool, *, active_only: bool = False) -> list[dict]:
    sql = f"SELECT {FIN_ASSET_FIELDS} FROM memory.fin_asset a"
    if active_only:
        sql += " WHERE a.is_active"
    sql += " ORDER BY a.kind, a.code"
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql)
    return [dict(r) for r in rows]


async def get_fin_asset(pool: asyncpg.Pool, asset_id: str) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {FIN_ASSET_FIELDS} FROM memory.fin_asset a WHERE a.id = $1::uuid", asset_id)
    return dict(row) if row else None


async def resolve_asset_id(pool: asyncpg.Pool, code: str) -> str | None:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT id::text FROM memory.fin_asset WHERE upper(code) = upper($1) ORDER BY chain NULLS FIRST LIMIT 1",
            code)


async def create_fin_asset(pool: asyncpg.Pool, fields: dict) -> dict:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO memory.fin_asset (code, name, kind, decimals, symbol, chain, contract_address)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id::text
            """,
            fields["code"], fields.get("name"), fields.get("kind") or "fiat",
            fields.get("decimals", 2), fields.get("symbol"),
            fields.get("chain"), fields.get("contract_address"),
        )
    return await get_fin_asset(pool, row["id"])


async def patch_fin_asset(pool: asyncpg.Pool, asset_id: str, fields: dict) -> dict | None:
    allowed = {"name", "symbol", "decimals", "chain", "contract_address", "is_active"}
    sets, args, i = [], [], 1
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k} = ${i}"); args.append(v); i += 1
    if not sets:
        return await get_fin_asset(pool, asset_id)
    args.append(asset_id)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE memory.fin_asset SET {', '.join(sets)} WHERE id = ${i}::uuid RETURNING id::text", *args)
    return await get_fin_asset(pool, asset_id) if row else None


# --- accounts -------------------------------------------------------

FIN_ACCOUNT_FIELDS = """
       a.id::text, a.name, a.kind, a.account_class,
       a.currency_asset_id::text AS currency_asset_id,
       cur.code AS currency_code,
       a.owner, a.account_group, a.institution, a.wallet_address, a.chain,
       a.person_id::text AS person_id, p.display_name AS person_name,
       a.visibility, a.owner_member_id::text AS owner_member_id,
       om.display_name AS owner_member_name,
       a.opening_balance::float8 AS opening_balance,
       a.include_in_net_worth, a.archived, a.sort, a.source_kind,
       a.created_at, a.updated_at
"""

FIN_ACCOUNT_FROM = """
  FROM memory.fin_account a
  LEFT JOIN memory.fin_asset cur ON cur.id = a.currency_asset_id
  LEFT JOIN canonical.person p ON p.id = a.person_id
                               AND p.merged_into IS NULL AND p.deleted_at IS NULL
  LEFT JOIN memory.fin_member om ON om.id = a.owner_member_id
 WHERE a.deleted_at IS NULL
"""


async def _account_balances(conn, account_ids: list[str]) -> dict[str, list[dict]]:
    """balances per account, with USD value via latest FX. Keyed by account id."""
    if not account_ids:
        return {}
    rows = await conn.fetch(
        """
        SELECT b.account_id::text AS account_id, b.asset_id::text AS asset_id,
               ast.code AS asset_code, ast.kind AS asset_kind,
               b.balance::float8 AS balance,
               (b.balance * (SELECT fr.rate FROM memory.fin_fx_rate fr
                              WHERE fr.asset_id = b.asset_id AND fr.quote = 'USD'
                              ORDER BY fr.rate_date DESC LIMIT 1))::float8 AS usd_value
          FROM memory.fin_account_balance b
          JOIN memory.fin_asset ast ON ast.id = b.asset_id
         WHERE b.account_id = ANY($1::uuid[])
         ORDER BY ast.kind, ast.code
        """,
        account_ids,
    )
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["account_id"], []).append(dict(r))
    return out


async def list_fin_accounts(pool: asyncpg.Pool, *, include_archived: bool = False,
                            account_class: str | None = None,
                            viewer: dict | None = None) -> list[dict]:
    args: list = []
    sql = f"SELECT {FIN_ACCOUNT_FIELDS} {FIN_ACCOUNT_FROM}"
    if not include_archived:
        sql += " AND a.archived = false"
    if account_class in ("operational", "investment"):
        sql += f" AND a.account_class = '{account_class}'"
    sql += account_visibility_clause("a", viewer, args)
    sql += " ORDER BY a.sort, a.account_group NULLS LAST, a.name"
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
        accts = [dict(r) for r in rows]
        bals = await _account_balances(conn, [a["id"] for a in accts])
    for a in accts:
        a["balances"] = bals.get(a["id"], [])
    return accts


async def get_fin_account(pool: asyncpg.Pool, account_id: str,
                          viewer: dict | None = None) -> dict | None:
    args: list = [account_id]
    clause = account_visibility_clause("a", viewer, args)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {FIN_ACCOUNT_FIELDS} {FIN_ACCOUNT_FROM} AND a.id = $1::uuid{clause}", *args)
        if row is None:
            return None
        acct = dict(row)
        bals = await _account_balances(conn, [acct["id"]])
    acct["balances"] = bals.get(acct["id"], [])
    return acct


async def create_fin_account(pool: asyncpg.Pool, fields: dict) -> dict:
    cur_id = fields.get("currency_asset_id")
    if not cur_id and fields.get("currency_code"):
        cur_id = await resolve_asset_id(pool, fields["currency_code"])
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO memory.fin_account
              (name, kind, account_class, currency_asset_id, owner, account_group, institution,
               wallet_address, chain, person_id, opening_balance, include_in_net_worth,
               source_kind, owner_member_id, visibility)
            VALUES ($1, $2, $12, $3::uuid, $4, $5, $6, $7, $8, $9::uuid, $10, $11, 'manual',
                    $13::uuid, $14)
            RETURNING id::text
            """,
            fields["name"], fields.get("kind") or "bank", cur_id,
            fields.get("owner") or "me", fields.get("account_group"),
            fields.get("institution"), fields.get("wallet_address"), fields.get("chain"),
            fields.get("person_id"), fields.get("opening_balance") or 0,
            fields.get("include_in_net_worth", True),
            fields.get("account_class") or "operational",
            fields.get("owner_member_id"), fields.get("visibility") or "shared",
        )
    return await get_fin_account(pool, row["id"])


async def patch_fin_account(pool: asyncpg.Pool, account_id: str, fields: dict) -> dict | None:
    allowed = {"name", "kind", "account_class", "currency_asset_id", "owner", "account_group",
               "institution", "wallet_address", "chain", "person_id",
               "opening_balance", "include_in_net_worth", "archived", "sort",
               "owner_member_id", "visibility"}
    casts = {"currency_asset_id": "::uuid", "person_id": "::uuid", "owner_member_id": "::uuid"}
    sets, args, i = [], [], 1
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k} = ${i}{casts.get(k, '')}"); args.append(v); i += 1
    if not sets:
        return await get_fin_account(pool, account_id)
    args.append(account_id)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE memory.fin_account SET {', '.join(sets)} "
            f"WHERE id = ${i}::uuid AND deleted_at IS NULL RETURNING id::text", *args)
    return await get_fin_account(pool, account_id) if row else None


async def soft_delete_fin_account(pool: asyncpg.Pool, account_id: str) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE memory.fin_account SET deleted_at = now() "
            "WHERE id = $1::uuid AND deleted_at IS NULL RETURNING id", account_id)
    return row is not None


# --- members + account visibility (sharing PR1) ---------------------

_FIN_MEMBER_FIELDS = """
       m.id::text, m.display_name, m.email, m.person_id::text AS person_id,
       p.display_name AS person_name, m.role, m.actor, m.is_active,
       m.created_at, m.updated_at
"""


async def list_fin_members(pool: asyncpg.Pool, *, active_only: bool = False) -> list[dict]:
    sql = (f"SELECT {_FIN_MEMBER_FIELDS} FROM memory.fin_member m "
           "LEFT JOIN canonical.person p ON p.id = m.person_id "
           "AND p.merged_into IS NULL AND p.deleted_at IS NULL")
    if active_only:
        sql += " WHERE m.is_active = true"
    sql += " ORDER BY (m.role = 'owner') DESC, m.display_name"
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql)
    return [dict(r) for r in rows]


async def get_fin_member(pool: asyncpg.Pool, member_id: str) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {_FIN_MEMBER_FIELDS} FROM memory.fin_member m "
            "LEFT JOIN canonical.person p ON p.id = m.person_id "
            "WHERE m.id = $1::uuid", member_id)
    return dict(row) if row else None


async def get_member_by_email(pool: asyncpg.Pool, email: str) -> dict | None:
    """Resolve an Authelia identity (email) to an active member."""
    if not email:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id::text, display_name, role, actor, is_active "
            "FROM memory.fin_member WHERE lower(email) = lower($1) AND is_active = true",
            email)
    return dict(row) if row else None


async def get_member_by_actor(pool: asyncpg.Pool, actor: str) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id::text, display_name, role, actor, is_active "
            "FROM memory.fin_member WHERE actor = $1", actor)
    return dict(row) if row else None


async def create_fin_member(pool: asyncpg.Pool, fields: dict) -> dict:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO memory.fin_member (display_name, email, person_id, role, actor, is_active)
            VALUES ($1, $2, $3::uuid, $4, $5, $6)
            RETURNING id::text
            """,
            fields["display_name"], fields.get("email"), fields.get("person_id"),
            fields.get("role") or "member", fields["actor"],
            fields.get("is_active", True))
    return await get_fin_member(pool, row["id"])


async def patch_fin_member(pool: asyncpg.Pool, member_id: str, fields: dict) -> dict | None:
    allowed = {"display_name", "email", "person_id", "role", "actor", "is_active"}
    casts = {"person_id": "::uuid"}
    sets, args, i = [], [], 1
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k} = ${i}{casts.get(k, '')}"); args.append(v); i += 1
    if not sets:
        return await get_fin_member(pool, member_id)
    args.append(member_id)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE memory.fin_member SET {', '.join(sets)}, updated_at = now() "
            f"WHERE id = ${i}::uuid RETURNING id::text", *args)
    return await get_fin_member(pool, member_id) if row else None


def is_uuid(s: str | None) -> bool:
    """True iff `s` is a well-formed UUID. Lets endpoints reject a malformed/empty
    account_id with a 400 instead of letting a `$1::uuid` cast 500 deep in a query."""
    if not s:
        return False
    try:
        uuid.UUID(str(s))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


# --- unified sharing primitive (entity-agnostic ACL) ----------------
# These three are the single source of truth for the "shared-by-default, owner
# can keep private" model. They work for ANY table with `visibility` +
# `owner_member_id` columns — fin_account today; contacts/tasks next — so a new
# shareable entity only needs those two columns to be scoped. The finance-named
# wrappers below preserve every existing call site (zero behaviour change).

def _viewer_sees_all(viewer: dict | None) -> bool:
    """True when no row filter applies: viewer is None (an app-owner — admin role,
    bot, workers) or has role 'owner'."""
    return not viewer or bool(viewer.get("is_owner")) or viewer.get("role") == "owner"


def visibility_clause(alias: str, viewer: dict | None, params: list) -> str:
    """AND-able SQL scoping any table aliased `alias` (must expose `visibility` +
    `owner_member_id`) to what `viewer` may see. '' when the viewer sees
    everything; else a member sees shared rows plus their own. Appends the
    member-id bind to `params` and references it positionally — the caller must
    build its query args from the same `params` list."""
    if _viewer_sees_all(viewer):
        return ""
    params.append(viewer["member_id"])
    n = len(params)
    return f" AND ({alias}.visibility = 'shared' OR {alias}.owner_member_id = ${n}::uuid)"


async def visible_entity_ids(pool: asyncpg.Pool, viewer: dict | None, *,
                             table: str = "memory.fin_account", id_col: str = "id",
                             soft_delete: bool = True) -> list[str] | None:
    """Ids of rows in `table` that `viewer` may see (shared or owned), for scoping
    derived/aggregate queries. None = no restriction (app-owner sees all). `table`
    is a trusted, code-supplied literal — never user input."""
    if _viewer_sees_all(viewer):
        return None
    where = "(visibility = 'shared' OR owner_member_id = $1::uuid)"
    if soft_delete:
        where = "deleted_at IS NULL AND " + where
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT {id_col}::text AS id FROM {table} WHERE {where}", viewer["member_id"])
    return [r["id"] for r in rows]


async def member_can_see(pool: asyncpg.Pool, viewer: dict | None, entity_id: str, *,
                         table: str = "memory.fin_account", id_col: str = "id",
                         soft_delete: bool = True) -> bool:
    """Write/own gate: may `viewer` act on this row of `table` at all?"""
    if _viewer_sees_all(viewer):
        return True
    sd = "deleted_at IS NULL AND " if soft_delete else ""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT 1 FROM {table} WHERE {id_col} = $1::uuid AND {sd}"
            "(visibility = 'shared' OR owner_member_id = $2::uuid)",
            entity_id, viewer["member_id"])
    return row is not None


# finance-named wrappers (existing call sites; unchanged behaviour) --------

def account_visibility_clause(alias: str, viewer: dict | None, params: list) -> str:
    return visibility_clause(alias, viewer, params)


async def visible_account_ids(pool: asyncpg.Pool, viewer: dict | None) -> list[str] | None:
    return await visible_entity_ids(pool, viewer, table="memory.fin_account")


async def member_can_see_account(pool: asyncpg.Pool, viewer: dict | None, account_id: str) -> bool:
    return await member_can_see(pool, viewer, account_id, table="memory.fin_account")


# --- approvals (sharing PR3) ----------------------------------------
# A member's edit/delete of a transaction that touches a SHARED account is queued
# for an owner instead of applying. A change to the member's OWN private items
# applies directly.

def member_owns_all_legs(member_id: str | None, account_ids: list, account_rows: list) -> bool:
    """Pure decision behind the approval gate (security-critical, unit-tested):
    True iff `member_id` is set, there is at least one leg, every leg account was
    found, and the member OWNS every one (owner_member_id == member_id) —
    regardless of shared/private. An owner edits their own account directly; any
    leg on an account they don't own (someone else's, or an unowned joint account)
    → False (→ needs approval)."""
    if not member_id or not account_ids:
        return False
    if len(account_rows) < len(account_ids):
        return False
    return all(r["owner_member_id"] == member_id for r in account_rows)


async def txn_owned_by_member(pool: asyncpg.Pool, member_id: str | None, txn: dict) -> bool:
    """True iff this member owns every account leg of `txn` — i.e. they may
    edit/delete it directly, no approval needed."""
    accs = [a for a in (txn.get("outflow_account_id"), txn.get("inflow_account_id")) if a]
    if not member_id or not accs:
        return False
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT owner_member_id::text AS owner_member_id "
            "FROM memory.fin_account WHERE id = ANY($1::uuid[])", accs)
    return member_owns_all_legs(member_id, accs, [dict(r) for r in rows])


_FIN_APPROVAL_FIELDS = """
       ap.id::text, ap.action, ap.target_table, ap.target_id::text AS target_id,
       ap.payload, ap.status, ap.note, ap.created_at, ap.decided_at,
       ap.requested_by::text AS requested_by, rm.display_name AS requested_by_name,
       ap.decided_by::text AS decided_by, dm.display_name AS decided_by_name,
       t.txn_date, t.payee_text,
       coalesce(t.outflow_amount, t.inflow_amount)::float8 AS amount,
       coalesce(oas.code, ias.code) AS asset_code,
       coalesce(oa.name, ia.name) AS account_name
"""
_FIN_APPROVAL_FROM = """
  FROM memory.fin_approval ap
  JOIN memory.fin_member rm ON rm.id = ap.requested_by
  LEFT JOIN memory.fin_member dm ON dm.id = ap.decided_by
  LEFT JOIN memory.fin_transaction t ON t.id = ap.target_id
  LEFT JOIN memory.fin_account oa ON oa.id = t.outflow_account_id
  LEFT JOIN memory.fin_account ia ON ia.id = t.inflow_account_id
  LEFT JOIN memory.fin_asset oas ON oas.id = t.outflow_asset_id
  LEFT JOIN memory.fin_asset ias ON ias.id = t.inflow_asset_id
"""


def _approval_row(r) -> dict:
    d = dict(r)
    p = d.get("payload")
    d["payload"] = json.loads(p) if isinstance(p, str) else (p or {})
    return d


def _coerce_txn_payload(payload: dict) -> dict:
    """A stored approval payload comes back from JSONB with typed fields flattened
    to strings (txn_date as an ISO string via default=str). Coerce them back to
    the types patch_fin_transaction/asyncpg expect before applying."""
    p = dict(payload or {})
    d = p.get("txn_date")
    if isinstance(d, str) and d:
        try:
            p["txn_date"] = date.fromisoformat(d[:10])
        except ValueError:
            pass
    for k in ("outflow_amount", "inflow_amount"):
        if isinstance(p.get(k), str):
            try:
                p[k] = float(p[k])
            except ValueError:
                pass
    return p


async def create_approval(pool: asyncpg.Pool, *, requested_by: str, action: str,
                          target_id: str, payload: dict) -> dict:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO memory.fin_approval (requested_by, action, target_id, payload)
            VALUES ($1::uuid, $2, $3::uuid, $4::jsonb)
            RETURNING id::text
            """, requested_by, action, target_id,
            # default=str so date/Decimal in the patch (e.g. txn_date) serialize
            json.dumps(payload or {}, default=str))
    return await get_approval(pool, row["id"])


async def get_approval(pool: asyncpg.Pool, approval_id: str) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {_FIN_APPROVAL_FIELDS} {_FIN_APPROVAL_FROM} WHERE ap.id = $1::uuid", approval_id)
    return _approval_row(row) if row else None


async def list_approvals(pool: asyncpg.Pool, *, status: str | None = None,
                         requester_member_id: str | None = None) -> list[dict]:
    """Owner sees all (optionally filtered by status); a member passes their own
    member id to see only their requests."""
    args: list = []
    where = []
    if status:
        args.append(status); where.append(f"ap.status = ${len(args)}")
    if requester_member_id:
        args.append(requester_member_id); where.append(f"ap.requested_by = ${len(args)}::uuid")
    sql = f"SELECT {_FIN_APPROVAL_FIELDS} {_FIN_APPROVAL_FROM}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY (ap.status = 'pending') DESC, ap.created_at DESC LIMIT 200"
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
    return [_approval_row(r) for r in rows]


async def count_pending_approvals(pool: asyncpg.Pool) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT count(*)::int FROM memory.fin_approval WHERE status = 'pending'") or 0


async def decide_approval(pool: asyncpg.Pool, approval_id: str, *, decided_by: str | None,
                          approve: bool, note: str | None = None) -> dict | None:
    """Approve (apply the stored action, then mark approved) or reject a pending
    request. No-op returning the row if it isn't pending. Returns None if absent."""
    ap = await get_approval(pool, approval_id)
    if ap is None:
        return None
    if ap["status"] != "pending":
        return ap
    if approve:
        if ap["action"] == "update_txn":
            await patch_fin_transaction(pool, ap["target_id"], _coerce_txn_payload(ap["payload"]))
        elif ap["action"] == "delete_txn":
            await soft_delete_fin_transaction(pool, ap["target_id"])
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE memory.fin_approval SET status = $2, decided_by = $3::uuid, "
            "decided_at = now(), note = $4, updated_at = now() WHERE id = $1::uuid",
            approval_id, "approved" if approve else "rejected", decided_by, note)
    return await get_approval(pool, approval_id)


# --- categories (config tree, opp_stage-style) ----------------------

FIN_CATEGORY_FIELDS = """
       c.key, c.label, c.parent_key, c.kind, c.sort, c.color, c.icon,
       (SELECT count(*)::int FROM memory.fin_transaction t
         WHERE t.category_key = c.key AND t.deleted_at IS NULL) AS in_use
"""


async def list_fin_categories(pool: asyncpg.Pool) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT {FIN_CATEGORY_FIELDS} FROM memory.fin_category c ORDER BY c.sort, c.label")
    return [dict(r) for r in rows]


def _slugify_cat(label: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    return s[:48] or "category"


async def create_fin_category(pool: asyncpg.Pool, fields: dict) -> dict:
    base = _slugify_cat(fields["label"])
    async with pool.acquire() as conn:
        key, n = base, 2
        while await conn.fetchval("SELECT 1 FROM memory.fin_category WHERE key = $1", key):
            key, n = f"{base}-{n}", n + 1
        sort = await conn.fetchval("SELECT coalesce(max(sort), 0) + 1 FROM memory.fin_category")
        await conn.execute(
            """
            INSERT INTO memory.fin_category (key, label, parent_key, kind, sort, color, icon, source_kind)
            VALUES ($1, $2, $3, $4, $5, $6, $7, 'manual')
            """,
            key, fields["label"], fields.get("parent_key"), fields.get("kind") or "expense",
            sort, fields.get("color") or "slate", fields.get("icon"),
        )
        row = await conn.fetchrow(f"SELECT {FIN_CATEGORY_FIELDS} FROM memory.fin_category c WHERE c.key = $1", key)
    return dict(row)


async def patch_fin_category(pool: asyncpg.Pool, key: str, fields: dict) -> dict | None:
    allowed = {"label", "parent_key", "kind", "color", "icon", "sort"}
    sets, args, i = [], [], 1
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k} = ${i}"); args.append(v); i += 1
    if not sets:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(f"SELECT {FIN_CATEGORY_FIELDS} FROM memory.fin_category c WHERE c.key = $1", key)
        return dict(row) if row else None
    args.append(key)
    async with pool.acquire() as conn:
        upd = await conn.fetchrow(
            f"UPDATE memory.fin_category SET {', '.join(sets)} WHERE key = ${i} RETURNING key", *args)
        if upd is None:
            return None
        row = await conn.fetchrow(f"SELECT {FIN_CATEGORY_FIELDS} FROM memory.fin_category c WHERE c.key = $1", key)
    return dict(row)


async def delete_fin_category(pool: asyncpg.Pool, key: str) -> str | None:
    """None on success, else error code ('not_found' | 'in_use')."""
    async with pool.acquire() as conn:
        if not await conn.fetchval("SELECT 1 FROM memory.fin_category WHERE key = $1", key):
            return "not_found"
        in_use = await conn.fetchval(
            "SELECT count(*) FROM memory.fin_transaction WHERE category_key = $1 AND deleted_at IS NULL", key)
        if in_use:
            return "in_use"
        await conn.execute("DELETE FROM memory.fin_category WHERE key = $1", key)
    return None


# --- payees ---------------------------------------------------------

FIN_PAYEE_FIELDS = """
       p.id::text, p.name,
       p.person_id::text AS person_id, per.display_name AS person_name,
       p.company_id::text AS company_id, co.name AS company_name,
       (SELECT count(*)::int FROM memory.fin_transaction t
         WHERE t.payee_id = p.id AND t.deleted_at IS NULL) AS txn_count
"""


async def list_fin_payees(pool: asyncpg.Pool, *, q: str | None = None, limit: int = 500) -> list[dict]:
    sql = f"""
    SELECT {FIN_PAYEE_FIELDS}
      FROM memory.fin_payee p
      LEFT JOIN canonical.person per ON per.id = p.person_id
                                     AND per.merged_into IS NULL AND per.deleted_at IS NULL
      LEFT JOIN memory.company co ON co.id = p.company_id
     WHERE ($1::text IS NULL OR p.name ILIKE '%'||$1||'%')
     ORDER BY txn_count DESC, p.name
     LIMIT $2
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, q, limit)
    return [dict(r) for r in rows]


async def create_fin_payee(pool: asyncpg.Pool, fields: dict) -> dict:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO memory.fin_payee (name, person_id, company_id, source_kind)
            VALUES ($1, $2::uuid, $3::uuid, 'manual')
            RETURNING id::text
            """, fields["name"], fields.get("person_id"), fields.get("company_id"))
        out = await conn.fetchrow(
            f"SELECT {FIN_PAYEE_FIELDS} FROM memory.fin_payee p "
            "LEFT JOIN canonical.person per ON per.id = p.person_id "
            "LEFT JOIN memory.company co ON co.id = p.company_id WHERE p.id = $1::uuid", row["id"])
    return dict(out)


async def patch_fin_payee(pool: asyncpg.Pool, payee_id: str, fields: dict) -> dict | None:
    allowed = {"name", "person_id", "company_id"}
    casts = {"person_id": "::uuid", "company_id": "::uuid"}
    sets, args, i = [], [], 1
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k} = ${i}{casts.get(k, '')}"); args.append(v); i += 1
    if not sets:
        return None
    args.append(payee_id)
    async with pool.acquire() as conn:
        upd = await conn.fetchrow(
            f"UPDATE memory.fin_payee SET {', '.join(sets)} WHERE id = ${i}::uuid RETURNING id", *args)
        if upd is None:
            return None
        out = await conn.fetchrow(
            f"SELECT {FIN_PAYEE_FIELDS} FROM memory.fin_payee p "
            "LEFT JOIN canonical.person per ON per.id = p.person_id "
            "LEFT JOIN memory.company co ON co.id = p.company_id WHERE p.id = $1::uuid", payee_id)
    return dict(out)


# --- budgets (per-category monthly targets) -------------------------

async def list_fin_budgets(pool: asyncpg.Pool, *, month_start) -> list[dict]:
    """Each category's monthly target + this month's actual spend (USD)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            WITH spend AS (
              SELECT t.category_key,
                     sum(t.outflow_amount *
                         coalesce((SELECT fr.rate FROM memory.fin_fx_rate fr
                                    WHERE fr.asset_id = t.outflow_asset_id AND fr.quote='USD'
                                    ORDER BY fr.rate_date DESC LIMIT 1), 0)) AS actual_usd
                FROM memory.fin_transaction t
               WHERE t.deleted_at IS NULL
                 AND t.outflow_account_id IS NOT NULL AND t.inflow_account_id IS NULL
                 AND t.txn_date >= $1::date
                 AND t.txn_date < ($1::date + interval '1 month')
               GROUP BY t.category_key
            )
            SELECT b.id::text, b.category_key, c.label AS category_label, c.color,
                   b.limit_usd::float8 AS limit_usd,
                   coalesce(s.actual_usd, 0)::float8 AS actual_usd
              FROM memory.fin_budget b
              JOIN memory.fin_category c ON c.key = b.category_key
              LEFT JOIN spend s ON s.category_key = b.category_key
             WHERE b.period = 'monthly'
             ORDER BY b.limit_usd DESC
            """, month_start)
    return [dict(r) for r in rows]


async def upsert_fin_budget(pool: asyncpg.Pool, *, category_key: str, limit_usd: float) -> dict:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO memory.fin_budget (category_key, period, limit_usd)
            VALUES ($1, 'monthly', $2)
            ON CONFLICT (category_key, period) DO UPDATE SET limit_usd = excluded.limit_usd
            """, category_key, limit_usd)
        row = await conn.fetchrow(
            "SELECT id::text, category_key, limit_usd::float8 AS limit_usd FROM memory.fin_budget "
            "WHERE category_key = $1 AND period = 'monthly'", category_key)
    return dict(row)


async def delete_fin_budget(pool: asyncpg.Pool, category_key: str) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "DELETE FROM memory.fin_budget WHERE category_key = $1 AND period = 'monthly' RETURNING id", category_key)
    return row is not None


# --- transactions (dual-leg ledger) ---------------------------------

FIN_TXN_FIELDS = """
       t.id::text, t.txn_date,
       CASE WHEN t.outflow_account_id IS NOT NULL AND t.inflow_account_id IS NOT NULL THEN 'transfer'
            WHEN t.outflow_account_id IS NOT NULL THEN 'expense'
            ELSE 'income' END AS txn_type,
       t.outflow_account_id::text AS outflow_account_id, oa.name AS outflow_account_name,
       t.outflow_asset_id::text AS outflow_asset_id, oas.code AS outflow_asset_code,
       t.outflow_amount::float8 AS outflow_amount,
       t.inflow_account_id::text AS inflow_account_id, ia.name AS inflow_account_name,
       t.inflow_asset_id::text AS inflow_asset_id, ias.code AS inflow_asset_code,
       t.inflow_amount::float8 AS inflow_amount,
       t.category_key, cat.label AS category_label, cat.color AS category_color,
       t.payee_id::text AS payee_id, t.payee_text,
       t.person_id::text AS person_id, per.display_name AS person_name,
       t.note, t.tags, t.source_kind, t.usd_value::float8 AS usd_value,
       t.created_at, t.updated_at
"""

FIN_TXN_FROM = """
  FROM memory.fin_transaction t
  LEFT JOIN memory.fin_account oa  ON oa.id  = t.outflow_account_id
  LEFT JOIN memory.fin_asset   oas ON oas.id = t.outflow_asset_id
  LEFT JOIN memory.fin_account ia  ON ia.id  = t.inflow_account_id
  LEFT JOIN memory.fin_asset   ias ON ias.id = t.inflow_asset_id
  LEFT JOIN memory.fin_category cat ON cat.key = t.category_key
  LEFT JOIN canonical.person per ON per.id = t.person_id
 WHERE t.deleted_at IS NULL
"""


async def list_fin_transactions(
    pool: asyncpg.Pool, *, account_id: str | None = None, category_key: str | None = None,
    txn_type: str | None = None, date_from=None, date_to=None, q: str | None = None,
    limit: int = 100, offset: int = 0, viewer: dict | None = None,
) -> list[dict]:
    args: list = [account_id, category_key, date_from, date_to, q, txn_type, limit, offset]
    # member scoping: a transaction is visible if either leg touches an account
    # the member may see (shared or owned). Owner/app-owner sees all.
    vis = ""
    if viewer and not viewer.get("is_owner") and viewer.get("role") != "owner":
        args.append(viewer["member_id"])
        n = len(args)
        vis = (f" AND ((oa.id IS NOT NULL AND (oa.visibility = 'shared' OR oa.owner_member_id = ${n}::uuid))"
               f"      OR (ia.id IS NOT NULL AND (ia.visibility = 'shared' OR ia.owner_member_id = ${n}::uuid)))")
    sql = f"""
    SELECT {FIN_TXN_FIELDS}
    {FIN_TXN_FROM}
      AND ($1::uuid IS NULL OR t.outflow_account_id = $1::uuid OR t.inflow_account_id = $1::uuid)
      AND ($2::text IS NULL OR t.category_key = $2::text)
      AND ($3::date IS NULL OR t.txn_date >= $3::date)
      AND ($4::date IS NULL OR t.txn_date <= $4::date)
      AND ($5::text IS NULL OR t.note ILIKE '%'||$5||'%' OR t.payee_text ILIKE '%'||$5||'%')
      AND ($6::text IS NULL OR
           (CASE WHEN t.outflow_account_id IS NOT NULL AND t.inflow_account_id IS NOT NULL THEN 'transfer'
                 WHEN t.outflow_account_id IS NOT NULL THEN 'expense' ELSE 'income' END) = $6::text){vis}
    ORDER BY t.txn_date DESC, t.created_at DESC
    LIMIT $7 OFFSET $8
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
    return [dict(r) for r in rows]


async def get_fin_transaction(pool: asyncpg.Pool, txn_id: str) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(f"SELECT {FIN_TXN_FIELDS} {FIN_TXN_FROM} AND t.id = $1::uuid", txn_id)
    return dict(row) if row else None


async def _default_asset_for_account(conn, account_id: str | None) -> str | None:
    if not account_id:
        return None
    return await conn.fetchval(
        "SELECT currency_asset_id::text FROM memory.fin_account WHERE id = $1::uuid", account_id)


async def create_fin_transaction(pool: asyncpg.Pool, fields: dict) -> dict:
    async with pool.acquire() as conn:
        out_asset = fields.get("outflow_asset_id") or await _default_asset_for_account(conn, fields.get("outflow_account_id"))
        in_asset = fields.get("inflow_asset_id") or await _default_asset_for_account(conn, fields.get("inflow_account_id"))
        row = await conn.fetchrow(
            """
            INSERT INTO memory.fin_transaction
              (txn_date, outflow_account_id, outflow_asset_id, outflow_amount,
               inflow_account_id, inflow_asset_id, inflow_amount,
               category_key, payee_id, payee_text, person_id, note, tags, source_kind)
            VALUES ($1, $2::uuid, $3::uuid, $4, $5::uuid, $6::uuid, $7,
                    $8, $9::uuid, $10, $11::uuid, $12, $13, coalesce($14,'manual'))
            RETURNING id::text
            """,
            fields["txn_date"], fields.get("outflow_account_id"), out_asset, fields.get("outflow_amount"),
            fields.get("inflow_account_id"), in_asset, fields.get("inflow_amount"),
            fields.get("category_key"), fields.get("payee_id"), fields.get("payee_text"),
            fields.get("person_id"), fields.get("note"), fields.get("tags") or [], fields.get("source_kind"),
        )
    return await get_fin_transaction(pool, row["id"])


_CHAIN_TXN_INSERT = """
INSERT INTO memory.fin_transaction
  (txn_date, outflow_account_id, outflow_asset_id, outflow_amount,
   inflow_account_id, inflow_asset_id, inflow_amount,
   payee_text, note, tags, category_key, usd_value, source_kind, source_ref)
VALUES ($1::date, $2::uuid, $3::uuid, $4, $5::uuid, $6::uuid, $7,
        $8, $9, $10, $11, $12, 'chain_tx', $13)
ON CONFLICT (source_kind, source_ref) WHERE source_ref IS NOT NULL DO NOTHING
RETURNING id
"""


async def insert_chain_transfer(
    pool: asyncpg.Pool, *, txn_date, account_id: str, asset_id: str, amount: float,
    direction: str, payee_text: str | None, note: str | None, source_ref: str,
    chain: str | None = None, usd_value: float | None = None,
    category_key: str | None = None,
) -> bool:
    """Idempotently record one on-chain transfer as a single-leg fin_transaction.
    direction='in' → inflow leg (received), 'out' → outflow leg (sent). `chain` is
    stored as a tag so the ledger can badge which chain the transfer is on;
    `usd_value` is the transfer's value in USD captured at block time; gas rows
    pass category_key='network-fees'. The balance view ignores these (holdings
    remain the source of truth); they exist purely as a categorizable activity
    feed. Returns True if a new row landed."""
    out_acc = account_id if direction == "out" else None
    in_acc = account_id if direction == "in" else None
    tags = [chain] if chain else []
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            _CHAIN_TXN_INSERT, txn_date,
            out_acc, asset_id if direction == "out" else None, amount if direction == "out" else None,
            in_acc, asset_id if direction == "in" else None, amount if direction == "in" else None,
            payee_text, note, tags, category_key, usd_value, source_ref)
    return row is not None


def chain_locked_side(source_kind: str | None, legs: list[tuple[str, str]],
                      account_kind: dict[str, str]) -> str | None:
    """Pure core of chain_txn_locked_side: given the source_kind, the populated
    ('side', account_id) legs, and an account_id→kind map, return which leg is the
    immutable on-chain one ('outflow'/'inflow') — the leg on the crypto wallet the
    transfer synced from — else None. Falls back to the first populated leg."""
    if source_kind != "chain_tx" or not legs:
        return None
    for side, acc in legs:
        if account_kind.get(acc) == "crypto_wallet":
            return side
    return legs[0][0]


async def chain_txn_locked_side(pool: asyncpg.Pool, txn: dict) -> str | None:
    """For an on-chain-imported transaction (source_kind='chain_tx'), which leg is
    the immutable on-chain one. That leg's account/asset/amount and the date are
    facts of the chain and must not be edited; the OTHER leg (plus
    category/payee/note/tags) is the editable classification layer used to
    reconcile the transfer (e.g. settle a debt). None for non-chain rows."""
    legs = [(s, txn.get(f"{s}_account_id")) for s in ("outflow", "inflow")]
    legs = [(s, a) for s, a in legs if a]
    if txn.get("source_kind") != "chain_tx" or not legs:
        return None
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id::text, kind FROM memory.fin_account WHERE id = ANY($1::uuid[])",
            [a for _, a in legs])
    return chain_locked_side("chain_tx", legs, {r["id"]: r["kind"] for r in rows})


async def patch_fin_transaction(pool: asyncpg.Pool, txn_id: str, fields: dict) -> dict | None:
    # Resolve a currency chosen by code (settle a USD debt from a USDT send) into
    # the concrete asset id the leg stores. Only applied when an id wasn't given.
    for side in ("outflow", "inflow"):
        code = fields.pop(f"{side}_asset_code", None)
        if code and not fields.get(f"{side}_asset_id"):
            fields[f"{side}_asset_id"] = await get_or_create_asset_by_code(
                pool, code, kind="fiat", decimals=2)
    # A leg's asset follows its account: when a side's account is (re)assigned
    # without an explicit asset, default the asset to that account's currency.
    # Otherwise editing a transfer's destination to a different-currency account
    # kept the OLD leg asset — e.g. changing the inflow to Sber (RUB) left it in
    # THB, so 4000 RUB landed as 4000 THB. Mirrors create_fin_transaction.
    async with pool.acquire() as conn:
        for side in ("outflow", "inflow"):
            acct = fields.get(f"{side}_account_id")
            if acct and not fields.get(f"{side}_asset_id"):
                fields[f"{side}_asset_id"] = await _default_asset_for_account(conn, acct)
    allowed = {"txn_date", "outflow_account_id", "outflow_asset_id", "outflow_amount",
               "inflow_account_id", "inflow_asset_id", "inflow_amount",
               "category_key", "payee_id", "payee_text", "person_id", "note", "tags"}
    casts = {"outflow_account_id": "::uuid", "outflow_asset_id": "::uuid",
             "inflow_account_id": "::uuid", "inflow_asset_id": "::uuid",
             "payee_id": "::uuid", "person_id": "::uuid"}
    sets, args, i = [], [], 1
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k} = ${i}{casts.get(k, '')}"); args.append(v); i += 1
    if not sets:
        return await get_fin_transaction(pool, txn_id)
    args.append(txn_id)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE memory.fin_transaction SET {', '.join(sets)} "
            f"WHERE id = ${i}::uuid AND deleted_at IS NULL RETURNING id::text", *args)
    return await get_fin_transaction(pool, txn_id) if row else None


async def soft_delete_fin_transaction(pool: asyncpg.Pool, txn_id: str) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE memory.fin_transaction SET deleted_at = now() "
            "WHERE id = $1::uuid AND deleted_at IS NULL RETURNING id", txn_id)
    return row is not None


# --- net worth + reports --------------------------------------------

async def _usd_thb_rate(conn) -> float:
    """How many THB per 1 USD (for the THB display toggle). 1 / (THB→USD)."""
    thb_usd = await conn.fetchval(
        """
        SELECT fr.rate::float8 FROM memory.fin_fx_rate fr
          JOIN memory.fin_asset a ON a.id = fr.asset_id
         WHERE a.code = 'THB' AND fr.quote = 'USD'
         ORDER BY fr.rate_date DESC LIMIT 1
        """)
    if thb_usd and thb_usd > 0:
        return 1.0 / thb_usd
    return 0.0


# friendly dashboard labels per account kind (when no explicit account_group)
_ACCOUNT_KIND_LABEL = {
    "bank": "Banks", "cash": "Cash", "crypto_wallet": "Crypto wallets",
    "cex": "Exchanges", "dex": "Exchanges", "brokerage": "Brokerage", "debt": "Debts",
}


async def net_worth(pool: asyncpg.Pool, *, viewer: dict | None = None) -> dict:
    args: list = []
    vis = account_visibility_clause("a", viewer, args)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            WITH bal AS (
              SELECT b.asset_id, ast.code AS asset_code, ast.kind AS asset_kind,
                     a.account_group, a.kind AS account_kind, a.owner, a.account_class, b.balance,
                     (SELECT fr.rate FROM memory.fin_fx_rate fr
                       WHERE fr.asset_id = b.asset_id AND fr.quote = 'USD'
                       ORDER BY fr.rate_date DESC LIMIT 1) AS usd_rate
                FROM memory.fin_account_balance b
                JOIN memory.fin_account a ON a.id = b.account_id
                JOIN memory.fin_asset ast ON ast.id = b.asset_id
               WHERE a.deleted_at IS NULL AND a.archived = false
                 AND a.include_in_net_worth = true{vis}
            )
            SELECT asset_id::text, asset_code, asset_kind, account_group, account_kind,
                   owner, account_class, balance::float8 AS balance,
                   (balance * coalesce(usd_rate, 0))::float8 AS usd_value
              FROM bal
            """, *args)
        usd_thb = await conn.fetchval(
            """
            SELECT fr.rate::float8 FROM memory.fin_fx_rate fr
              JOIN memory.fin_asset a ON a.id = fr.asset_id
             WHERE a.code = 'THB' AND fr.quote = 'USD'
             ORDER BY fr.rate_date DESC LIMIT 1
            """)
    usd_thb_rate = (1.0 / usd_thb) if (usd_thb and usd_thb > 0) else 0.0

    by_asset: dict[str, dict] = {}
    by_group: dict[str, float] = {}
    by_owner: dict[str, float] = {}
    total = operational = investment = 0.0
    for r in rows:
        usd = r["usd_value"] or 0.0
        total += usd
        if r["account_class"] == "investment":
            investment += usd
        else:
            operational += usd
        a = by_asset.setdefault(r["asset_id"], {
            "asset_id": r["asset_id"], "asset_code": r["asset_code"],
            "asset_kind": r["asset_kind"], "balance": 0.0, "usd_value": 0.0})
        a["balance"] += r["balance"] or 0.0
        a["usd_value"] += usd
        grp = r["account_group"] or _ACCOUNT_KIND_LABEL.get(r["account_kind"], "Other")
        by_group[grp] = by_group.get(grp, 0.0) + usd
        by_owner[r["owner"]] = by_owner.get(r["owner"], 0.0) + usd
    return {
        "total_usd": total,
        "total_thb": total * usd_thb_rate,
        "operational_usd": operational,
        "investment_usd": investment,
        "usd_thb_rate": usd_thb_rate,
        "by_asset": sorted(by_asset.values(), key=lambda x: -x["usd_value"]),
        "by_group": [{"group": k, "usd_value": v} for k, v in sorted(by_group.items(), key=lambda x: -x[1])],
        "by_owner": [{"group": k, "usd_value": v} for k, v in sorted(by_owner.items(), key=lambda x: -x[1])],
    }


async def report_spending_by_category(pool: asyncpg.Pool, *, date_from, date_to,
                                      viewer: dict | None = None) -> list[dict]:
    """Expense totals by category, converted to USD via the outflow asset's rate.
    A member only sees spend on accounts visible to them (NULL ids = all)."""
    vis_ids = await visible_account_ids(pool, viewer)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT coalesce(t.category_key, 'uncategorized') AS category_key,
                   coalesce(cat.label, 'Uncategorized') AS label,
                   coalesce(cat.color, 'slate') AS color,
                   sum(t.outflow_amount *
                       coalesce((SELECT fr.rate FROM memory.fin_fx_rate fr
                                  WHERE fr.asset_id = t.outflow_asset_id AND fr.quote='USD'
                                  ORDER BY fr.rate_date DESC LIMIT 1), 0))::float8 AS usd_total,
                   count(*)::int AS txn_count
              FROM memory.fin_transaction t
              LEFT JOIN memory.fin_category cat ON cat.key = t.category_key
             WHERE t.deleted_at IS NULL
               AND t.outflow_account_id IS NOT NULL AND t.inflow_account_id IS NULL
               AND t.txn_date >= $1::date AND t.txn_date <= $2::date
               AND ($3::uuid[] IS NULL OR t.outflow_account_id = ANY($3::uuid[]))
             GROUP BY 1, 2, 3
             ORDER BY usd_total DESC
            """, date_from, date_to, vis_ids)
    return [dict(r) for r in rows]


async def report_cashflow(pool: asyncpg.Pool, *, months: int = 6,
                          viewer: dict | None = None) -> list[dict]:
    """Income vs expense per month (USD), last `months` months. A member only
    sees flows on accounts visible to them (NULL ids = all)."""
    vis_ids = await visible_account_ids(pool, viewer)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            WITH legs AS (
              SELECT date_trunc('month', txn_date)::date AS m,
                     'expense' AS dir,
                     outflow_amount AS amt, outflow_asset_id AS asset_id
                FROM memory.fin_transaction
               WHERE deleted_at IS NULL AND outflow_account_id IS NOT NULL AND inflow_account_id IS NULL
                 AND ($2::uuid[] IS NULL OR outflow_account_id = ANY($2::uuid[]))
              UNION ALL
              SELECT date_trunc('month', txn_date)::date, 'income',
                     inflow_amount, inflow_asset_id
                FROM memory.fin_transaction
               WHERE deleted_at IS NULL AND inflow_account_id IS NOT NULL AND outflow_account_id IS NULL
                 AND ($2::uuid[] IS NULL OR inflow_account_id = ANY($2::uuid[]))
            )
            SELECT to_char(m, 'YYYY-MM') AS month, dir,
                   sum(amt * coalesce((SELECT fr.rate FROM memory.fin_fx_rate fr
                                        WHERE fr.asset_id = legs.asset_id AND fr.quote='USD'
                                        ORDER BY fr.rate_date DESC LIMIT 1),0))::float8 AS usd_total
              FROM legs
             WHERE m >= date_trunc('month', CURRENT_DATE) - ($1::int - 1) * interval '1 month'
             GROUP BY 1, 2
             ORDER BY 1
            """, months, vis_ids)
    # pivot to {month, income, expense}
    by_month: dict[str, dict] = {}
    for r in rows:
        m = by_month.setdefault(r["month"], {"month": r["month"], "income": 0.0, "expense": 0.0})
        m[r["dir"]] = r["usd_total"] or 0.0
    return list(by_month.values())


# --- FX rates -------------------------------------------------------

async def upsert_fx_rate(pool: asyncpg.Pool, *, asset_id: str, rate: float, rate_date,
                         quote: str = "USD", source: str = "manual") -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO memory.fin_fx_rate (asset_id, quote, rate, rate_date, source)
            VALUES ($1::uuid, $2, $3, $4, $5)
            ON CONFLICT (asset_id, quote, rate_date)
            DO UPDATE SET rate = excluded.rate, source = excluded.source
            """, asset_id, quote, rate, rate_date, source)


# --- import batches -------------------------------------------------

FIN_IMPORT_FIELDS = """
       b.id::text, b.kind, b.filename, b.account_id::text AS account_id,
       b.status, b.row_count, b.parsed, b.note, b.created_at, b.decided_at
"""


async def list_fin_import_batches(pool: asyncpg.Pool, *, status: str | None = None) -> list[dict]:
    sql = f"SELECT {FIN_IMPORT_FIELDS} FROM memory.fin_import_batch b"
    args = []
    if status:
        sql += " WHERE b.status = $1"; args.append(status)
    sql += " ORDER BY b.created_at DESC LIMIT 50"
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
    out = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get("parsed"), str):
            d["parsed"] = json.loads(d["parsed"])
        out.append(d)
    return out


async def get_fin_import_batch(pool: asyncpg.Pool, batch_id: str) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(f"SELECT {FIN_IMPORT_FIELDS} FROM memory.fin_import_batch b WHERE b.id = $1::uuid", batch_id)
    if row is None:
        return None
    d = dict(row)
    if isinstance(d.get("parsed"), str):
        d["parsed"] = json.loads(d["parsed"])
    return d


async def create_fin_import_batch(pool: asyncpg.Pool, *, kind: str, filename: str | None,
                                  account_id: str | None, parsed: dict, note: str | None = None) -> dict:
    rows = parsed.get("rows") or []
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO memory.fin_import_batch (kind, filename, account_id, parsed, row_count, note)
            VALUES ($1, $2, $3::uuid, $4::jsonb, $5, $6)
            RETURNING id::text
            """, kind, filename, account_id, json.dumps(parsed), len(rows), note)
    return await get_fin_import_batch(pool, row["id"])


async def mark_import_batch(pool: asyncpg.Pool, batch_id: str, status: str) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE memory.fin_import_batch SET status = $2, decided_at = now() "
            "WHERE id = $1::uuid AND status = 'pending' RETURNING id", batch_id, status)
    return row is not None


# --- planned / recurring transactions -------------------------------

def _add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def advance_planned(d: date, freq: str, byweekday) -> date:
    """Next occurrence after `d` for the schedule."""
    if freq == "daily":
        return d + timedelta(days=1)
    if freq == "weekly":
        days = sorted({int(x) for x in (byweekday or [])}) or [d.weekday()]
        for i in range(1, 8):
            nd = d + timedelta(days=i)
            if nd.weekday() in days:
                return nd
        return d + timedelta(days=7)
    if freq == "monthly":
        return _add_months(d, 1)
    if freq == "yearly":
        return _add_months(d, 12)
    return d + timedelta(days=1)


FIN_PLANNED_FIELDS = """
       pl.id::text, pl.name,
       pl.outflow_account_id::text AS outflow_account_id, oa.name AS outflow_account_name,
       pl.outflow_asset_id::text AS outflow_asset_id, oas.code AS outflow_asset_code,
       pl.outflow_amount::float8 AS outflow_amount,
       pl.inflow_account_id::text AS inflow_account_id, ia.name AS inflow_account_name,
       pl.inflow_asset_id::text AS inflow_asset_id, ias.code AS inflow_asset_code,
       pl.inflow_amount::float8 AS inflow_amount,
       pl.category_key, cat.label AS category_label,
       pl.payee_text, pl.note,
       pl.freq, pl.byweekday, pl.next_date, pl.auto_post, pl.active,
       CASE WHEN pl.outflow_account_id IS NOT NULL AND pl.inflow_account_id IS NOT NULL THEN 'transfer'
            WHEN pl.outflow_account_id IS NOT NULL THEN 'expense' ELSE 'income' END AS txn_type
"""

FIN_PLANNED_FROM = """
  FROM memory.fin_planned_transaction pl
  LEFT JOIN memory.fin_account oa  ON oa.id  = pl.outflow_account_id
  LEFT JOIN memory.fin_asset   oas ON oas.id = pl.outflow_asset_id
  LEFT JOIN memory.fin_account ia  ON ia.id  = pl.inflow_account_id
  LEFT JOIN memory.fin_asset   ias ON ias.id = pl.inflow_asset_id
  LEFT JOIN memory.fin_category cat ON cat.key = pl.category_key
 WHERE pl.deleted_at IS NULL
"""


async def list_fin_planned(pool: asyncpg.Pool) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT {FIN_PLANNED_FIELDS} {FIN_PLANNED_FROM} ORDER BY pl.active DESC, pl.next_date")
    return [dict(r) for r in rows]


async def get_fin_planned(pool: asyncpg.Pool, planned_id: str) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(f"SELECT {FIN_PLANNED_FIELDS} {FIN_PLANNED_FROM} AND pl.id = $1::uuid", planned_id)
    return dict(row) if row else None


async def create_fin_planned(pool: asyncpg.Pool, fields: dict) -> dict:
    async with pool.acquire() as conn:
        out_asset = fields.get("outflow_asset_id") or await _default_asset_for_account(conn, fields.get("outflow_account_id"))
        in_asset = fields.get("inflow_asset_id") or await _default_asset_for_account(conn, fields.get("inflow_account_id"))
        row = await conn.fetchrow(
            """
            INSERT INTO memory.fin_planned_transaction
              (name, outflow_account_id, outflow_asset_id, outflow_amount,
               inflow_account_id, inflow_asset_id, inflow_amount,
               category_key, payee_text, note, freq, byweekday, next_date, auto_post)
            VALUES ($1, $2::uuid, $3::uuid, $4, $5::uuid, $6::uuid, $7,
                    $8, $9, $10, $11, $12, $13, $14)
            RETURNING id::text
            """,
            fields.get("name"), fields.get("outflow_account_id"), out_asset, fields.get("outflow_amount"),
            fields.get("inflow_account_id"), in_asset, fields.get("inflow_amount"),
            fields.get("category_key"), fields.get("payee_text"), fields.get("note"),
            fields.get("freq") or "monthly", fields.get("byweekday") or [],
            fields["next_date"], fields.get("auto_post", True),
        )
    return await get_fin_planned(pool, row["id"])


async def patch_fin_planned(pool: asyncpg.Pool, planned_id: str, fields: dict) -> dict | None:
    allowed = {"name", "outflow_account_id", "outflow_asset_id", "outflow_amount",
               "inflow_account_id", "inflow_asset_id", "inflow_amount",
               "category_key", "payee_text", "note", "freq", "byweekday",
               "next_date", "auto_post", "active"}
    casts = {"outflow_account_id": "::uuid", "outflow_asset_id": "::uuid",
             "inflow_account_id": "::uuid", "inflow_asset_id": "::uuid"}
    sets, args, i = [], [], 1
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k} = ${i}{casts.get(k, '')}"); args.append(v); i += 1
    if not sets:
        return await get_fin_planned(pool, planned_id)
    args.append(planned_id)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE memory.fin_planned_transaction SET {', '.join(sets)} "
            f"WHERE id = ${i}::uuid AND deleted_at IS NULL RETURNING id", *args)
    return await get_fin_planned(pool, planned_id) if row else None


async def soft_delete_fin_planned(pool: asyncpg.Pool, planned_id: str) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE memory.fin_planned_transaction SET deleted_at = now() "
            "WHERE id = $1::uuid AND deleted_at IS NULL RETURNING id", planned_id)
    return row is not None


_PLANNED_INSERT_TXN = """
INSERT INTO memory.fin_transaction
  (txn_date, outflow_account_id, outflow_asset_id, outflow_amount,
   inflow_account_id, inflow_asset_id, inflow_amount,
   category_key, payee_text, note, source_kind, source_ref)
VALUES ($1::date, $2::uuid, $3::uuid, $4, $5::uuid, $6::uuid, $7,
        $8, $9, $10, 'planned', $11)
ON CONFLICT (source_kind, source_ref) WHERE source_ref IS NOT NULL DO NOTHING
"""


async def _post_one_occurrence(conn, p: dict, occ_date: date) -> None:
    await conn.execute(
        _PLANNED_INSERT_TXN, occ_date,
        p["outflow_account_id"], p["outflow_asset_id"], p["outflow_amount"],
        p["inflow_account_id"], p["inflow_asset_id"], p["inflow_amount"],
        p["category_key"], p["payee_text"], p["note"], f"{p['id']}:{occ_date.isoformat()}")


async def post_planned_now(pool: asyncpg.Pool, planned_id: str) -> dict | None:
    """Materialize the current next_date occurrence and advance the schedule."""
    async with pool.acquire() as conn:
        p = await conn.fetchrow(
            "SELECT * FROM memory.fin_planned_transaction WHERE id = $1::uuid AND deleted_at IS NULL", planned_id)
        if p is None:
            return None
        async with conn.transaction():
            await _post_one_occurrence(conn, dict(p), p["next_date"])
            nd = advance_planned(p["next_date"], p["freq"], p["byweekday"])
            await conn.execute(
                "UPDATE memory.fin_planned_transaction SET next_date = $2 WHERE id = $1::uuid", planned_id, nd)
    return await get_fin_planned(pool, planned_id)


# --- holdings (snapshot positions: crypto wallets + brokerage) ------

FIN_HOLDING_FIELDS = """
       h.id::text, h.account_id::text AS account_id, acc.name AS account_name,
       h.asset_id::text AS asset_id, ast.code AS asset_code, ast.kind AS asset_kind,
       h.chain, h.quantity::float8 AS quantity, h.cost_basis_usd::float8 AS cost_basis_usd,
       h.source, h.updated_at,
       (SELECT fr.rate::float8 FROM memory.fin_fx_rate fr
         WHERE fr.asset_id = h.asset_id AND fr.quote='USD'
         ORDER BY fr.rate_date DESC LIMIT 1) AS usd_rate
"""


async def list_fin_holdings(pool: asyncpg.Pool, *, account_id: str | None = None,
                            viewer: dict | None = None) -> list[dict]:
    args: list = [account_id]
    vis = account_visibility_clause("acc", viewer, args)
    sql = f"""
    SELECT {FIN_HOLDING_FIELDS}
      FROM memory.fin_holding h
      JOIN memory.fin_account acc ON acc.id = h.account_id AND acc.deleted_at IS NULL
      JOIN memory.fin_asset ast ON ast.id = h.asset_id
     WHERE ($1::uuid IS NULL OR h.account_id = $1::uuid){vis}
     ORDER BY acc.name, (h.quantity *
        coalesce((SELECT fr.rate FROM memory.fin_fx_rate fr
                   WHERE fr.asset_id = h.asset_id AND fr.quote='USD'
                   ORDER BY fr.rate_date DESC LIMIT 1), 0)) DESC, ast.code
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
    out = []
    for r in rows:
        d = dict(r)
        d["usd_value"] = (d["quantity"] or 0) * (d.pop("usd_rate") or 0)
        out.append(d)
    return out


async def upsert_fin_holding(pool: asyncpg.Pool, *, account_id: str, asset_id: str,
                             quantity: float, cost_basis_usd: float | None = None,
                             source: str = "manual", chain: str | None = None) -> dict | None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO memory.fin_holding (account_id, asset_id, chain, quantity, cost_basis_usd, source)
            VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6)
            ON CONFLICT (account_id, asset_id, COALESCE(chain, ''))
            DO UPDATE SET quantity = excluded.quantity,
                          cost_basis_usd = COALESCE(excluded.cost_basis_usd, memory.fin_holding.cost_basis_usd),
                          source = excluded.source, updated_at = now()
            """, account_id, asset_id, chain, quantity, cost_basis_usd, source)
    rows = await list_fin_holdings(pool, account_id=account_id)
    return next((r for r in rows if r["asset_id"] == asset_id and (r.get("chain") or None) == (chain or None)), None)


async def clear_chain_holdings(pool: asyncpg.Pool, account_id: str) -> None:
    """Drop an account's chain-synced holdings (manual ones are kept) — called
    before re-inserting a fresh on-chain snapshot."""
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM memory.fin_holding WHERE account_id = $1::uuid AND source = 'chain'", account_id)


async def delete_fin_holding(pool: asyncpg.Pool, holding_id: str) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("DELETE FROM memory.fin_holding WHERE id = $1::uuid RETURNING id", holding_id)
    return row is not None


# --- investments: FIFO cost lots (Phase 2) ---------------------------------

_FIN_LOT_FIELDS = """
    l.id::text, l.account_id::text, l.asset_id::text,
    ast.code AS asset_code, ast.kind AS asset_kind,
    l.open_date, l.quantity::float8 AS quantity,
    l.cost_per_unit_usd::float8 AS cost_per_unit_usd, l.note, l.created_at
"""

_FIN_SALE_FIELDS = """
    s.id::text, s.account_id::text, s.asset_id::text,
    ast.code AS asset_code, s.sale_date, s.quantity::float8 AS quantity,
    s.proceeds_per_unit_usd::float8 AS proceeds_per_unit_usd, s.note, s.created_at
"""


async def list_fin_lots(pool: asyncpg.Pool, *, account_id: str | None = None,
                        asset_id: str | None = None) -> list[dict]:
    sql = f"""
    SELECT {_FIN_LOT_FIELDS}
      FROM memory.fin_lot l
      JOIN memory.fin_asset ast ON ast.id = l.asset_id
     WHERE ($1::uuid IS NULL OR l.account_id = $1::uuid)
       AND ($2::uuid IS NULL OR l.asset_id = $2::uuid)
     ORDER BY l.open_date, l.created_at
    """
    async with pool.acquire() as conn:
        return [dict(r) for r in await conn.fetch(sql, account_id, asset_id)]


async def insert_fin_lot(pool: asyncpg.Pool, *, account_id: str, asset_id: str,
                         open_date, quantity, cost_per_unit_usd,
                         note: str | None = None) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO memory.fin_lot (account_id, asset_id, open_date, quantity, "
            "cost_per_unit_usd, note) VALUES ($1::uuid,$2::uuid,$3,$4,$5,$6) RETURNING id::text",
            account_id, asset_id, open_date, quantity, cost_per_unit_usd, note)
    if not row:
        return None
    lots = await list_fin_lots(pool, account_id=account_id, asset_id=asset_id)
    return next((l for l in lots if l["id"] == row["id"]), None)


async def delete_fin_lot(pool: asyncpg.Pool, lot_id: str) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("DELETE FROM memory.fin_lot WHERE id = $1::uuid RETURNING id", lot_id)
    return row is not None


async def list_fin_sales(pool: asyncpg.Pool, *, account_id: str | None = None,
                         asset_id: str | None = None) -> list[dict]:
    sql = f"""
    SELECT {_FIN_SALE_FIELDS}
      FROM memory.fin_lot_sale s
      JOIN memory.fin_asset ast ON ast.id = s.asset_id
     WHERE ($1::uuid IS NULL OR s.account_id = $1::uuid)
       AND ($2::uuid IS NULL OR s.asset_id = $2::uuid)
     ORDER BY s.sale_date, s.created_at
    """
    async with pool.acquire() as conn:
        return [dict(r) for r in await conn.fetch(sql, account_id, asset_id)]


async def insert_fin_sale(pool: asyncpg.Pool, *, account_id: str, asset_id: str,
                          sale_date, quantity, proceeds_per_unit_usd,
                          note: str | None = None) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO memory.fin_lot_sale (account_id, asset_id, sale_date, quantity, "
            "proceeds_per_unit_usd, note) VALUES ($1::uuid,$2::uuid,$3,$4,$5,$6) RETURNING id::text",
            account_id, asset_id, sale_date, quantity, proceeds_per_unit_usd, note)
    if not row:
        return None
    sales = await list_fin_sales(pool, account_id=account_id, asset_id=asset_id)
    return next((s for s in sales if s["id"] == row["id"]), None)


async def delete_fin_sale(pool: asyncpg.Pool, sale_id: str) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("DELETE FROM memory.fin_lot_sale WHERE id = $1::uuid RETURNING id", sale_id)
    return row is not None


async def _engine_lots_sales(pool: asyncpg.Pool, account_id: str, asset_id: str | None):
    """Fetch lots+sales as Decimal (raw NUMERIC, NOT float8) and build engine
    objects — exact FIFO math needs Decimal."""
    from . import fin_lots
    a_filter = "AND asset_id = $2::uuid" if asset_id else ""
    params = (account_id, asset_id) if asset_id else (account_id,)
    async with pool.acquire() as conn:
        lot_rows = await conn.fetch(
            f"SELECT id::text, asset_id::text, open_date, quantity, cost_per_unit_usd "
            f"FROM memory.fin_lot WHERE account_id = $1::uuid {a_filter}", *params)
        sale_rows = await conn.fetch(
            f"SELECT id::text, asset_id::text, sale_date, quantity, proceeds_per_unit_usd "
            f"FROM memory.fin_lot_sale WHERE account_id = $1::uuid {a_filter}", *params)
    lots = [fin_lots.Lot(r["id"], r["open_date"], r["quantity"], r["cost_per_unit_usd"]) for r in lot_rows]
    sales = [fin_lots.Sale(r["id"], r["sale_date"], r["quantity"], r["proceeds_per_unit_usd"]) for r in sale_rows]
    return lots, sales


async def fin_remaining_quantity(pool: asyncpg.Pool, *, account_id: str, asset_id: str):
    """Open quantity for one (account, asset) via FIFO replay — used to reject an
    oversell before persisting it. Returns a Decimal."""
    from . import fin_lots
    lots, sales = await _engine_lots_sales(pool, account_id, asset_id)
    return fin_lots.remaining_quantity(lots, sales)


async def compute_fin_positions(pool: asyncpg.Pool, *, account_id: str) -> list[dict]:
    """FIFO position + realized/unrealized P&L for every asset with lots or sales
    in an account. Current price is the latest USD fin_fx_rate."""
    from decimal import Decimal
    from . import fin_lots

    by_lots, by_sales = await _engine_lots_sales_grouped(pool, account_id)
    asset_ids = set(by_lots) | set(by_sales)
    if not asset_ids:
        return []
    async with pool.acquire() as conn:
        arows = await conn.fetch(
            """
            SELECT ast.id::text AS asset_id, ast.code, ast.kind,
                   (SELECT fr.rate::float8 FROM memory.fin_fx_rate fr
                      WHERE fr.asset_id = ast.id AND fr.quote='USD'
                      ORDER BY fr.rate_date DESC LIMIT 1) AS price_usd
              FROM memory.fin_asset ast
             WHERE ast.id = ANY($1::uuid[])
            """, list(asset_ids))
    assets = {a["asset_id"]: a for a in arows}

    out = []
    for asset_id in asset_ids:
        a = assets.get(asset_id, {"code": "?", "kind": "stock", "price_usd": None})
        pos = fin_lots.fifo_position(by_lots.get(asset_id, []), by_sales.get(asset_id, []))
        price = a["price_usd"]
        price_dec = Decimal(str(price)) if price is not None else None
        avg = pos.avg_cost_per_unit_usd
        out.append({
            "account_id": account_id, "asset_id": asset_id,
            "asset_code": a["code"], "asset_kind": a["kind"],
            "remaining_quantity": float(pos.remaining_quantity),
            "open_cost_usd": float(pos.open_cost_usd),
            "avg_cost_per_unit_usd": float(avg) if avg is not None else None,
            "current_price_usd": price,
            "market_value_usd": float(pos.remaining_quantity * price_dec) if price_dec is not None else None,
            "unrealized_gain_usd": float(pos.unrealized_gain_usd(price_dec)) if price_dec is not None else None,
            "realized_gain_usd": float(pos.realized_gain_usd),
            "lots": [
                {"id": st.lot.id, "open_date": st.lot.open_date,
                 "quantity": float(st.lot.quantity),
                 "remaining_quantity": float(st.remaining_quantity),
                 "cost_per_unit_usd": float(st.lot.cost_per_unit_usd)}
                for st in pos.lots
            ],
        })
    out.sort(key=lambda p: (p["market_value_usd"] or 0), reverse=True)
    return out


async def _engine_lots_sales_grouped(pool: asyncpg.Pool, account_id: str):
    """Lots/sales for an account as engine objects grouped by asset_id (Decimal)."""
    from collections import defaultdict
    from . import fin_lots
    async with pool.acquire() as conn:
        lot_rows = await conn.fetch(
            "SELECT id::text, asset_id::text, open_date, quantity, cost_per_unit_usd "
            "FROM memory.fin_lot WHERE account_id = $1::uuid", account_id)
        sale_rows = await conn.fetch(
            "SELECT id::text, asset_id::text, sale_date, quantity, proceeds_per_unit_usd "
            "FROM memory.fin_lot_sale WHERE account_id = $1::uuid", account_id)
    lots = defaultdict(list)
    sales = defaultdict(list)
    for r in lot_rows:
        lots[r["asset_id"]].append(
            fin_lots.Lot(r["id"], r["open_date"], r["quantity"], r["cost_per_unit_usd"]))
    for r in sale_rows:
        sales[r["asset_id"]].append(
            fin_lots.Sale(r["id"], r["sale_date"], r["quantity"], r["proceeds_per_unit_usd"]))
    return lots, sales


async def get_or_create_asset_by_code(pool: asyncpg.Pool, code: str, *, kind: str = "crypto",
                                      decimals: int = 8, chain: str | None = None,
                                      name: str | None = None) -> str:
    """Resolve an asset id by code (case-insensitive), creating it if missing.
    Used by crypto sync to map on-chain tokens to assets."""
    async with pool.acquire() as conn:
        existing = await conn.fetchval(
            "SELECT id::text FROM memory.fin_asset WHERE upper(code) = upper($1) ORDER BY chain NULLS FIRST LIMIT 1", code)
        if existing:
            return existing
        row = await conn.fetchrow(
            """
            INSERT INTO memory.fin_asset (code, name, kind, decimals, chain)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (code, COALESCE(chain, '')) DO UPDATE SET code = excluded.code
            RETURNING id::text
            """, code.upper(), name or code, kind, decimals, chain)
    return row["id"]


async def crypto_wallet_accounts(pool: asyncpg.Pool) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id::text, name, wallet_address, chain, account_class, kind
              FROM memory.fin_account
             WHERE deleted_at IS NULL AND wallet_address IS NOT NULL AND chain IS NOT NULL
            """)
    return [dict(r) for r in rows]


# --- historical USD price cache (crypto-sync++ per-tx cost at block time) ----
# Block-time USD values are priced from fin_fx_rate keyed by the block's date.
# The crypto worker backfills missing (asset, date) rows from CoinGecko's daily
# history endpoint via these helpers, so a value is fetched once and reused.

async def get_fx_rate_on(pool: asyncpg.Pool, asset_id: str, on_date) -> float | None:
    """USD-per-unit rate for an asset on a specific date, if already cached."""
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            SELECT rate::float8 FROM memory.fin_fx_rate
             WHERE asset_id = $1::uuid AND quote = 'USD' AND rate_date = $2
             LIMIT 1
            """, asset_id, on_date)


async def cache_fx_rate(pool: asyncpg.Pool, asset_id: str, rate: float, on_date,
                        source: str = "coingecko_hist") -> None:
    """Insert a historical USD rate if absent. DO NOTHING (never clobber an
    existing rate — e.g. the daily worker's same-day spot price)."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO memory.fin_fx_rate (asset_id, quote, rate, rate_date, source)
            VALUES ($1::uuid, 'USD', $2, $3, $4)
            ON CONFLICT (asset_id, quote, rate_date) DO NOTHING
            """, asset_id, rate, on_date, source)


# --- per-wallet sync health (crypto-sync++) ---------------------------------

async def upsert_wallet_sync(pool: asyncpg.Pool, account_id: str, *, status: str,
                             error: str | None, summary: dict) -> None:
    """Stamp a wallet account's latest sync outcome. Un-audited by design."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO memory.fin_wallet_sync (account_id, last_synced_at, status, error, summary, updated_at)
            VALUES ($1::uuid, now(), $2, $3, $4::jsonb, now())
            ON CONFLICT (account_id) DO UPDATE
              SET last_synced_at = now(), status = excluded.status,
                  error = excluded.error, summary = excluded.summary, updated_at = now()
            """, account_id, status, error, json.dumps(summary or {}))


async def list_wallet_sync(pool: asyncpg.Pool) -> dict[str, dict]:
    """{account_id: {last_synced_at, status, error, summary}} for every synced wallet."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT account_id::text, last_synced_at, status, error, summary FROM memory.fin_wallet_sync")
    out: dict[str, dict] = {}
    for r in rows:
        d = dict(r)
        s = d.get("summary")
        d["summary"] = json.loads(s) if isinstance(s, str) else (s or {})
        out[d["account_id"]] = d
    return out


async def wallets_summary(pool: asyncpg.Pool, *, viewer: dict | None = None) -> dict:
    """Multi-wallet aggregation: holdings rolled up per asset across every
    on-chain wallet (+ a per-wallet breakdown), plus per-wallet gas/transfer
    activity and sync health. Read-only reporting view, scoped to the viewer."""
    hold_args: list = []
    hold_vis = account_visibility_clause("a", viewer, hold_args)
    vis_ids = await visible_account_ids(pool, viewer)
    async with pool.acquire() as conn:
        hold = await conn.fetch(
            f"""
            SELECT a.id::text AS account_id, a.name AS account_name, a.chain AS account_chain,
                   a.account_class, ast.code AS asset_code, ast.kind AS asset_kind,
                   h.chain AS holding_chain, h.quantity::float8 AS quantity,
                   (SELECT fr.rate::float8 FROM memory.fin_fx_rate fr
                     WHERE fr.asset_id = h.asset_id AND fr.quote = 'USD'
                     ORDER BY fr.rate_date DESC LIMIT 1) AS usd_rate
              FROM memory.fin_holding h
              JOIN memory.fin_account a ON a.id = h.account_id
              JOIN memory.fin_asset ast ON ast.id = h.asset_id
             WHERE a.deleted_at IS NULL AND a.archived = false
               AND a.wallet_address IS NOT NULL{hold_vis}
            """, *hold_args)
        # gas spend (USD) + transfer counts per wallet, from the activity feed
        stats = await conn.fetch(
            """
            SELECT acc AS account_id,
                   count(*) FILTER (WHERE source_kind = 'chain_tx')::int AS transfers,
                   coalesce(sum(usd_value) FILTER (WHERE category_key = 'network-fees'), 0)::float8 AS gas_usd,
                   count(*) FILTER (WHERE category_key = 'network-fees')::int AS gas_count
              FROM (
                SELECT coalesce(outflow_account_id, inflow_account_id) AS acc,
                       source_kind, category_key, usd_value
                  FROM memory.fin_transaction
                 WHERE deleted_at IS NULL AND source_kind = 'chain_tx'
              ) t
             WHERE ($1::uuid[] IS NULL OR acc = ANY($1::uuid[]))
             GROUP BY acc
            """, vis_ids)
    health = await list_wallet_sync(pool)

    by_asset: dict[str, dict] = {}
    by_wallet: dict[str, dict] = {}
    total_usd = 0.0
    for r in hold:
        rate = r["usd_rate"] or 0.0
        usd = (r["quantity"] or 0.0) * rate
        total_usd += usd
        a = by_asset.setdefault(r["asset_code"], {
            "asset_code": r["asset_code"], "asset_kind": r["asset_kind"],
            "quantity": 0.0, "usd_value": 0.0, "wallets": []})
        a["quantity"] += r["quantity"] or 0.0
        a["usd_value"] += usd
        a["wallets"].append({
            "account_id": r["account_id"], "account_name": r["account_name"],
            "chain": r["holding_chain"] or r["account_chain"],
            "quantity": r["quantity"] or 0.0, "usd_value": usd})
        w = by_wallet.setdefault(r["account_id"], {
            "account_id": r["account_id"], "account_name": r["account_name"],
            "chain": r["account_chain"], "usd_value": 0.0,
            "gas_usd": 0.0, "transfers": 0})
        w["usd_value"] += usd

    stat_by_acc = {s["account_id"]: s for s in stats}
    # wallets with activity but no current holdings still deserve a row
    for acc_id, s in stat_by_acc.items():
        if acc_id and acc_id not in by_wallet:
            by_wallet[acc_id] = {
                "account_id": acc_id, "account_name": None, "chain": None,
                "usd_value": 0.0, "gas_usd": 0.0, "transfers": 0}
    total_gas_usd = 0.0
    for acc_id, w in by_wallet.items():
        s = stat_by_acc.get(acc_id)
        if s:
            w["gas_usd"] = s["gas_usd"] or 0.0
            w["transfers"] = s["transfers"] or 0
        total_gas_usd += w["gas_usd"]
        h = health.get(acc_id)
        w["last_synced_at"] = h["last_synced_at"] if h else None
        w["sync_status"] = h["status"] if h else None
        w["sync_error"] = h["error"] if h else None

    return {
        "total_usd": total_usd,
        "total_gas_usd": total_gas_usd,
        "by_asset": sorted(by_asset.values(), key=lambda x: -x["usd_value"]),
        "by_wallet": sorted(by_wallet.values(), key=lambda x: -x["usd_value"]),
    }


# --- bot transaction capture resolution -----------------------------

async def resolve_account_fuzzy(pool: asyncpg.Pool, name: str) -> dict | None:
    """Best-effort match a spoken account name to a fin_account."""
    n = (name or "").strip()
    if not n:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT a.id::text, a.name, a.currency_asset_id::text AS currency_asset_id
              FROM memory.fin_account a
             WHERE a.deleted_at IS NULL AND a.archived = false
               AND (a.name ILIKE '%'||$1||'%' OR similarity(a.name, $1) > 0.3)
             ORDER BY (a.name ILIKE $1) DESC, similarity(a.name, $1) DESC
             LIMIT 1
            """, n)
    return dict(row) if row else None


async def resolve_category_fuzzy(pool: asyncpg.Pool, label: str, *, kind: str) -> dict | None:
    """Match a plain-words category to a fin_category (expense/income-appropriate)."""
    lab = (label or "").strip()
    if not lab:
        return None
    want = "expense" if kind == "expense" else "income"
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT key, label FROM memory.fin_category
             WHERE kind IN ($2, 'both')
               AND (label ILIKE '%'||$1||'%' OR similarity(label, $1) > 0.3)
             ORDER BY (label ILIKE $1) DESC, similarity(label, $1) DESC
             LIMIT 1
            """, lab, want)
    return dict(row) if row else None


async def default_fin_account(pool: asyncpg.Pool) -> dict | None:
    """A sensible default account for a captured transaction (first cash, else
    first bank, else any)."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id::text, name, currency_asset_id::text AS currency_asset_id
              FROM memory.fin_account
             WHERE deleted_at IS NULL AND archived = false
             ORDER BY CASE kind WHEN 'cash' THEN 0 WHEN 'bank' THEN 1 ELSE 2 END, sort, name
             LIMIT 1
            """)
    return dict(row) if row else None


async def fin_accounts_for_picker(pool: asyncpg.Pool) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id::text, name FROM memory.fin_account "
            "WHERE deleted_at IS NULL AND archived = false ORDER BY sort, name LIMIT 12")
    return [dict(r) for r in rows]


async def fin_categories_search(pool: asyncpg.Pool, q: str, kind: str) -> list[dict]:
    want = "expense" if kind == "expense" else "income"
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT key, label FROM memory.fin_category
             WHERE kind IN ($2, 'both') AND label ILIKE '%'||$1||'%'
             ORDER BY label LIMIT 6
            """, q, want)
    return [dict(r) for r in rows]


# --- mail (read-only Gmail reader — backlog #2 Phase 1) -------------
# Reads the already-ingested raw.gmail_message. Admin-only surface (email is
# sensitive; budget members never reach /api/mail). Threads are grouped by
# thread_id (falling back to message_id for imports without one).

async def list_mail_accounts(pool: asyncpg.Pool) -> list[dict]:
    """Accounts with mail, plus can_send = the account's granted OAuth scopes
    (raw.gmail_account.scopes, captured at re-consent) include gmail.send — so
    the compose From-picker offers only accounts that can actually send."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT m.account_email, count(*)::int AS messages,
                   max(m.internal_date) AS last_at,
                   COALESCE('https://www.googleapis.com/auth/gmail.send' = ANY(a.scopes), false) AS can_send
              FROM raw.gmail_message m
              LEFT JOIN raw.gmail_account a ON a.email = m.account_email
             GROUP BY m.account_email, a.scopes
             ORDER BY last_at DESC NULLS LAST
            """)
    return [dict(r) for r in rows]


# Map Gmail's own labelIds → our triage buckets (auto-triage Phase A — leans on
# Google's existing classification, which is already stored in labels[]). Priority
# order matters (a message can carry several CATEGORY_* labels).
_MAIL_CATEGORY_CASE = """
  CASE
    WHEN 'SPAM' = ANY(labels) THEN 'spam'
    WHEN 'CATEGORY_PROMOTIONS' = ANY(labels) THEN 'promotions'
    WHEN 'CATEGORY_UPDATES'    = ANY(labels) THEN 'updates'
    WHEN 'CATEGORY_FORUMS'     = ANY(labels) THEN 'forums'
    WHEN 'CATEGORY_SOCIAL'     = ANY(labels) THEN 'social'
    ELSE 'personal'
  END
"""


def _unsubscribe_https(list_unsubscribe: str | None) -> str | None:
    """The https URL inside a List-Unsubscribe header (RFC 2369), if any. The
    header is one or more `<...>` entries (https and/or mailto); we surface only
    an https link for a safe one-click in the UI (never auto-POST, never mailto)."""
    if not list_unsubscribe:
        return None
    for part in re.findall(r"<([^>]+)>", list_unsubscribe):
        p = part.strip()
        if p.lower().startswith("https://"):
            return p
    return None


def _unsubscribe_mailto(list_unsubscribe: str | None) -> str | None:
    """The mailto: target inside a List-Unsubscribe header, if any (fallback when
    a sender offers no https link — the user completes it in their mail client)."""
    if not list_unsubscribe:
        return None
    for part in re.findall(r"<([^>]+)>", list_unsubscribe):
        p = part.strip()
        if p.lower().startswith("mailto:"):
            return p
    return None


def _unsubscribe_one_click(list_unsub: str | None, list_unsub_post: str | None) -> bool:
    """True when the sender offers RFC 8058 one-click unsubscribe: a
    `List-Unsubscribe-Post: List-Unsubscribe=One-Click` header alongside an https
    List-Unsubscribe URL. Such a URL can be POSTed to silently (no browser)."""
    return bool(_unsubscribe_https(list_unsub)) and "one-click" in (list_unsub_post or "").lower()


def triage_signals(headers: dict | None) -> dict:
    """Header heuristics for mail auto-triage (Phase B). High precision / low
    recall by design — these COMPLEMENT the Gmail-label category, never replace
    it. Keys are the lowercased raw headers the fetcher persists in
    payload['headers']. RFC 3834: Auto-Submitted != 'no' ⇒ automated."""
    h = headers or {}
    auto = (h.get("auto-submitted") or "").strip().lower()
    prec = (h.get("precedence") or "").strip().lower()
    list_unsub = h.get("list-unsubscribe")
    return {
        "automated": bool(auto) and auto != "no",
        "bulk": prec in ("bulk", "list", "junk"),
        "mailing_list": bool((h.get("list-id") or "").strip()),
        "has_unsubscribe": bool((list_unsub or "").strip()),
        "unsubscribe_url": _unsubscribe_https(list_unsub),
    }


def _parse_headers(raw) -> dict:
    """payload->'headers' comes back from the codec-less pool as a JSON string
    (or None / already-dict). Normalize to a dict."""
    if isinstance(raw, str):
        try:
            return json.loads(raw) or {}
        except Exception:
            return {}
    return raw or {}


def _dmarc_authenticated(headers: dict | None) -> bool:
    """True when the message passed DMARC (aligned + authenticated). Such senders
    are legitimate — Rspamd, run post-delivery without full MTA context, over-flags
    them, so we never treat a DMARC-pass sender as spam."""
    ar = ((headers or {}).get("authentication-results") or "").lower()
    return "dmarc=pass" in ar


def _mail_is_spam(labels, spam_action: str | None, headers: dict | None) -> bool:
    """The spam verdict surfaced in the UI. Precision-first (Gmail already filtered
    this mail, so we only add high-confidence spam):
      - Gmail's own SPAM label always counts; else
      - only Rspamd `reject` (score ≥ the reject threshold), and only when the
        sender is NOT DMARC-authenticated. Drops the noisy soft-reject/add-header
        band and never flags authenticated senders (Google, Alibaba, …)."""
    if "SPAM" in (labels or []):
        return True
    if (spam_action or "").strip().lower() == "reject":
        return not _dmarc_authenticated(headers)
    return False


async def list_mail_threads(pool: asyncpg.Pool, *, q: str | None = None,
                            account: str | None = None, category: str | None = None,
                            archived: str = "hide", starred: bool = False,
                            content: str | None = None,
                            trashed: str = "hide", snoozed: str = "hide",
                            limit: int = 50, offset: int = 0) -> list[dict]:
    """Latest message per thread (newest first) with snippet, unread flag, msg
    count, triage `category` (from Gmail labels), and local `archived`/`starred`/
    `trashed`/`snoozed_until` state. `archived`/`trashed`/`snoozed`: 'hide'
    (default) | 'only' | 'all'; `starred`: only-starred. A snoozed thread whose
    time has passed counts as unread again (resurface — the list sorts by
    internal_date, so without this an expired snooze returns silently buried).

    Structured for speed (P1): the head-of-thread dedup touches only cheap columns
    (the gmail_message_thread_idx expression index makes it an index scan, no sort),
    filters + LIMIT run on those, and the expensive per-row work — body snippet
    regexp, payload headers detoast, per-thread count — happens ONLY for the page's
    ~50 rows. Was ~2.4s at 29.5k messages; the old shape regexp'd every body."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            WITH heads AS (
              SELECT DISTINCT ON (account_email, COALESCE(thread_id, message_id))
                     COALESCE(thread_id, message_id) AS thread_key,
                     account_email, message_id, internal_date, labels,
                     {_MAIL_CATEGORY_CASE} AS category
                FROM raw.gmail_message
               WHERE ($1::text IS NULL OR account_email = $1)
                 AND ($2::text IS NULL OR subject ILIKE '%'||$2||'%'
                      OR from_address ILIKE '%'||$2||'%' OR from_name ILIKE '%'||$2||'%'
                      OR body_text ILIKE '%'||$2||'%')
               ORDER BY account_email, COALESCE(thread_id, message_id), internal_date DESC
            ),
            page AS (
              SELECT h.*, sp.score AS spam_score, sp.action AS spam_action,
                     mc.content_class, mc.confidence AS content_confidence,
                     (('UNREAD' = ANY(h.labels) AND NOT COALESCE(ms.read, false))
                      OR (ms.snoozed_until IS NOT NULL AND ms.snoozed_until <= now())) AS unread,
                     COALESCE(ms.archived, false) AS archived,
                     COALESCE(ms.starred, false) AS starred,
                     COALESCE(ms.trashed, false) AS trashed,
                     ms.snoozed_until
                FROM heads h
                LEFT JOIN memory.mail_state ms
                  ON ms.account_email = h.account_email AND ms.thread_key = h.thread_key
                LEFT JOIN memory.mail_spam sp
                  ON sp.account_email = h.account_email AND sp.message_id = h.message_id
                LEFT JOIN memory.mail_class mc
                  ON mc.account_email = h.account_email AND mc.message_id = h.message_id
               WHERE ($5::text IS NULL OR h.category = $5
                      -- Spam also surfaces high-confidence Rspamd rejects (Phase B2)
                      -- that are NOT DMARC-authenticated. The guard clauses keep the
                      -- header probe to the handful of reject rows.
                      OR ($5 = 'spam' AND sp.action = 'reject' AND NOT EXISTS (
                            SELECT 1 FROM raw.gmail_message mh
                             WHERE mh.account_email = h.account_email
                               AND mh.message_id = h.message_id
                               AND COALESCE(mh.payload->'headers'->>'authentication-results','')
                                   ILIKE '%dmarc=pass%')))
                 AND ($6::text = 'all'
                      OR ($6::text = 'only' AND COALESCE(ms.archived, false))
                      OR ($6::text = 'hide' AND NOT COALESCE(ms.archived, false)))
                 AND ($7::bool IS NOT TRUE OR COALESCE(ms.starred, false))
                 AND ($8::text IS NULL OR mc.content_class = $8)
                 AND ($9::text = 'all'
                      OR ($9::text = 'only' AND COALESCE(ms.trashed, false))
                      OR ($9::text = 'hide' AND NOT COALESCE(ms.trashed, false)))
                 AND ($10::text = 'all'
                      OR ($10::text = 'only' AND ms.snoozed_until > now())
                      OR ($10::text = 'hide'
                          AND (ms.snoozed_until IS NULL OR ms.snoozed_until <= now())))
               ORDER BY h.internal_date DESC
               LIMIT $3 OFFSET $4
            )
            SELECT p.thread_key, p.account_email, m.from_address, m.from_name,
                   m.subject,
                   left(regexp_replace(left(COALESCE(m.body_text,''), 1000), '\\s+', ' ', 'g'), 200) AS snippet,
                   p.internal_date, p.labels, p.category,
                   m.payload->'headers' AS headers,
                   p.spam_score, p.spam_action, p.content_class, p.content_confidence,
                   p.unread, p.archived, p.starred, p.trashed, p.snoozed_until,
                   (SELECT count(*) FROM raw.gmail_message m2
                     WHERE m2.account_email = p.account_email
                       AND COALESCE(m2.thread_id, m2.message_id) = p.thread_key)::int AS msg_count
              FROM page p
              JOIN raw.gmail_message m
                ON m.account_email = p.account_email AND m.message_id = p.message_id
             ORDER BY p.internal_date DESC
            """, account, q, limit, offset, category, archived, starred, content,
            trashed, snoozed)
    out = []
    for r in rows:
        d = dict(r)
        hdrs = _parse_headers(d.pop("headers", None))
        d["signals"] = triage_signals(hdrs)
        d["is_spam"] = _mail_is_spam(d.get("labels"), d.get("spam_action"), hdrs)
        out.append(d)
    return out


async def list_mail_senders(pool: asyncpg.Pool, *, account: str | None = None,
                            q: str | None = None, limit: int = 100) -> list[dict]:
    """Mail grouped by sender, highest volume first — the garbage-cleanup view.
    Per sender: message/unread counts, last date, the newest message's content
    class, and an unsubscribe URL when the newest list-carrying message offers
    one. The laterals stay cheap via gmail_message_from_idx."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            WITH senders AS (
              SELECT g.from_address,
                     max(g.from_name) AS from_name,
                     count(*)::int AS messages,
                     count(*) FILTER (WHERE 'UNREAD' = ANY(g.labels))::int AS unread,
                     max(g.internal_date) AS last_at
                FROM raw.gmail_message g
                LEFT JOIN memory.mail_state ms
                  ON ms.account_email = g.account_email
                 AND ms.thread_key = COALESCE(g.thread_id, g.message_id)
               WHERE g.from_address IS NOT NULL
                 AND NOT COALESCE(ms.trashed, false)   -- hide already-trashed mail
                 AND ($1::text IS NULL OR g.account_email = $1)
                 AND ($2::text IS NULL OR g.from_address ILIKE '%'||$2||'%'
                      OR g.from_name ILIKE '%'||$2||'%')
               GROUP BY g.from_address
               ORDER BY messages DESC, last_at DESC
               LIMIT $3
            )
            SELECT s.*, cls.content_class, lu.list_unsubscribe
              FROM senders s
              LEFT JOIN LATERAL (
                SELECT c.content_class
                  FROM raw.gmail_message m2
                  LEFT JOIN memory.mail_class c
                    ON c.account_email = m2.account_email AND c.message_id = m2.message_id
                 WHERE m2.from_address = s.from_address
                   AND ($1::text IS NULL OR m2.account_email = $1)
                 ORDER BY m2.internal_date DESC LIMIT 1) cls ON true
              LEFT JOIN LATERAL (
                SELECT m3.payload->'headers'->>'list-unsubscribe' AS list_unsubscribe
                  FROM raw.gmail_message m3
                 WHERE m3.from_address = s.from_address
                   AND ($1::text IS NULL OR m3.account_email = $1)
                   AND m3.payload->'headers' ? 'list-unsubscribe'
                 ORDER BY m3.internal_date DESC LIMIT 1) lu ON true
             ORDER BY s.messages DESC, s.last_at DESC
            """, account, q, max(1, min(limit, 300)))
    out = []
    for r in rows:
        d = dict(r)
        d["unsubscribe_url"] = _unsubscribe_https(d.pop("list_unsubscribe", None))
        out.append(d)
    return out


async def set_sender_state(pool: asyncpg.Pool, *, from_address: str,
                           account: str | None = None,
                           archived: bool | None = None, read: bool | None = None,
                           trashed: bool | None = None) -> int:
    """Bulk-apply the app-local overlay (archive/read/trash) to every thread that
    has a message from `from_address`. Returns the number of threads touched."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            INSERT INTO memory.mail_state (account_email, thread_key, archived, read, trashed, updated_at)
            SELECT DISTINCT account_email, COALESCE(thread_id, message_id),
                   COALESCE($3, false), COALESCE($4, false), COALESCE($5, false), now()
              FROM raw.gmail_message
             WHERE from_address = $1
               AND ($2::text IS NULL OR account_email = $2)
            ON CONFLICT (account_email, thread_key) DO UPDATE
              SET archived = COALESCE($3, memory.mail_state.archived),
                  read     = COALESCE($4, memory.mail_state.read),
                  trashed  = COALESCE($5, memory.mail_state.trashed),
                  updated_at = now()
            """, from_address, account, archived, read, trashed)
    try:
        return int(result.split()[-1])
    except (ValueError, IndexError):
        return 0


async def sender_message_ids(pool: asyncpg.Pool, *, from_address: str,
                             account: str | None = None) -> dict[str, list[str]]:
    """Gmail message ids from this sender, grouped per account — the unit the
    Gmail batchModify push works in. Only accounts with an active token."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT m.account_email, array_agg(m.message_id) AS ids
              FROM raw.gmail_message m
              JOIN raw.gmail_account a
                ON a.email = m.account_email AND a.status = 'active'
                   AND a.refresh_token IS NOT NULL
             WHERE m.from_address = $1
               AND ($2::text IS NULL OR m.account_email = $2)
             GROUP BY m.account_email
            """, from_address, account)
    return {r["account_email"]: list(r["ids"]) for r in rows}


async def list_mail_cleanup_senders(pool: asyncpg.Pool, *, account: str | None = None,
                                    q: str | None = None, limit: int = 200) -> list[dict]:
    """The Mail-Cleanup view: senders highest-volume first, enriched with the
    signals the cleanup page ranks on — first/last date, the newest message's
    content class, an unsubscribe URL + whether it's RFC 8058 one-click, and
    `replied` (have I ever emailed this address — the safety flag that keeps real
    contacts out of the bulk-delete recommendations). `replied` is a substring
    match against my Sent recipients so it survives 'Name <addr>' formatting."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            WITH sent_addrs AS (
              SELECT DISTINCT lower(t) AS addr
                FROM raw.gmail_message, unnest(to_addresses) AS t
               WHERE 'SENT' = ANY(labels)
                 AND ($1::text IS NULL OR account_email = $1)
            ), senders AS (
              SELECT g.from_address,
                     max(g.from_name) AS from_name,
                     count(*)::int AS messages,
                     count(*) FILTER (WHERE 'UNREAD' = ANY(g.labels))::int AS unread,
                     min(g.internal_date) AS first_at,
                     max(g.internal_date) AS last_at
                FROM raw.gmail_message g
                LEFT JOIN memory.mail_state ms
                  ON ms.account_email = g.account_email
                 AND ms.thread_key = COALESCE(g.thread_id, g.message_id)
               WHERE g.from_address IS NOT NULL
                 AND NOT COALESCE(ms.trashed, false)   -- already-cleared mail drops out
                 AND ($1::text IS NULL OR g.account_email = $1)
                 AND ($2::text IS NULL OR g.from_address ILIKE '%'||$2||'%'
                      OR g.from_name ILIKE '%'||$2||'%')
               GROUP BY g.from_address
               ORDER BY messages DESC, last_at DESC
               LIMIT $3
            )
            SELECT s.*, cls.content_class,
                   lu.list_unsubscribe, lu.list_unsubscribe_post,
                   COALESCE(p.keep, false) AS kept,
                   COALESCE(p.clear, false) AS on_clear_list,
                   EXISTS (SELECT 1 FROM sent_addrs sa
                            WHERE sa.addr LIKE '%'||lower(s.from_address)||'%') AS replied
              FROM senders s
              LEFT JOIN memory.mail_sender_pref p ON p.from_address = s.from_address
              LEFT JOIN LATERAL (
                SELECT c.content_class
                  FROM raw.gmail_message m2
                  LEFT JOIN memory.mail_class c
                    ON c.account_email = m2.account_email AND c.message_id = m2.message_id
                 WHERE m2.from_address = s.from_address
                   AND ($1::text IS NULL OR m2.account_email = $1)
                 ORDER BY m2.internal_date DESC LIMIT 1) cls ON true
              LEFT JOIN LATERAL (
                SELECT m3.payload->'headers'->>'list-unsubscribe' AS list_unsubscribe,
                       m3.payload->'headers'->>'list-unsubscribe-post' AS list_unsubscribe_post
                  FROM raw.gmail_message m3
                 WHERE m3.from_address = s.from_address
                   AND ($1::text IS NULL OR m3.account_email = $1)
                   AND m3.payload->'headers' ? 'list-unsubscribe'
                 ORDER BY m3.internal_date DESC LIMIT 1) lu ON true
             ORDER BY s.messages DESC, s.last_at DESC
            """, account, q, max(1, min(limit, 500)))
    out = []
    for r in rows:
        d = dict(r)
        lu = d.pop("list_unsubscribe", None)
        d["unsubscribe_url"] = _unsubscribe_https(lu) or _unsubscribe_mailto(lu)
        d["one_click"] = _unsubscribe_one_click(lu, d.pop("list_unsubscribe_post", None))
        out.append(d)
    return out


async def sender_unsubscribe_target(pool: asyncpg.Pool, *, from_address: str,
                                    account: str | None = None) -> dict | None:
    """The newest List-Unsubscribe target for a sender → {https, mailto,
    one_click}. None when the sender offers no unsubscribe at all."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT payload->'headers'->>'list-unsubscribe' AS lu,
                   payload->'headers'->>'list-unsubscribe-post' AS lup
              FROM raw.gmail_message
             WHERE from_address = $1
               AND ($2::text IS NULL OR account_email = $2)
               AND payload->'headers' ? 'list-unsubscribe'
             ORDER BY internal_date DESC LIMIT 1
            """, from_address, account)
    if not row:
        return None
    return {
        "https": _unsubscribe_https(row["lu"]),
        "mailto": _unsubscribe_mailto(row["lu"]),
        "one_click": _unsubscribe_one_click(row["lu"], row["lup"]),
    }


async def set_mail_sender_pref(pool: asyncpg.Pool, *, from_address: str,
                               keep: bool | None = None, clear: bool | None = None) -> dict:
    """Set a sender's keep/clear preference. The two are mutually exclusive — the
    flag just turned ON wins; a row that ends up both-false is deleted. Only the
    flags passed (non-None) change. Returns the resulting {keep, clear}."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT keep, clear FROM memory.mail_sender_pref WHERE from_address = $1 FOR UPDATE",
                from_address)
            new_keep = (bool(row["keep"]) if row else False) if keep is None else bool(keep)
            new_clear = (bool(row["clear"]) if row else False) if clear is None else bool(clear)
            if keep:      # turning keep on drops it from the clear list, and vice-versa
                new_clear = False
            if clear:
                new_keep = False
            if not new_keep and not new_clear:
                await conn.execute(
                    "DELETE FROM memory.mail_sender_pref WHERE from_address = $1", from_address)
            else:
                await conn.execute(
                    """INSERT INTO memory.mail_sender_pref (from_address, keep, clear, updated_at)
                       VALUES ($1, $2, $3, now())
                       ON CONFLICT (from_address) DO UPDATE
                         SET keep = $2, clear = $3, updated_at = now()""",
                    from_address, new_keep, new_clear)
    return {"keep": new_keep, "clear": new_clear}


async def list_mail_clear_senders(pool: asyncpg.Pool, *, account: str | None = None) -> list[dict]:
    """Every sender on the clear list with its remaining (non-trashed) message
    count + unsubscribe target — the batch to bulk-trash. NOT volume-limited (the
    user hand-picks these while reading), and a fully-trashed sender drops out."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            WITH senders AS (
              SELECT g.from_address,
                     max(g.from_name) AS from_name,
                     count(*)::int AS messages,
                     count(*) FILTER (WHERE 'UNREAD' = ANY(g.labels))::int AS unread,
                     min(g.internal_date) AS first_at,
                     max(g.internal_date) AS last_at
                FROM memory.mail_sender_pref p
                JOIN raw.gmail_message g ON g.from_address = p.from_address
                LEFT JOIN memory.mail_state ms
                  ON ms.account_email = g.account_email
                 AND ms.thread_key = COALESCE(g.thread_id, g.message_id)
               WHERE p.clear = true
                 AND NOT COALESCE(ms.trashed, false)
                 AND ($1::text IS NULL OR g.account_email = $1)
               GROUP BY g.from_address
            )
            SELECT s.*, cls.content_class,
                   lu.list_unsubscribe, lu.list_unsubscribe_post
              FROM senders s
              LEFT JOIN LATERAL (
                SELECT c.content_class
                  FROM raw.gmail_message m2
                  LEFT JOIN memory.mail_class c
                    ON c.account_email = m2.account_email AND c.message_id = m2.message_id
                 WHERE m2.from_address = s.from_address
                   AND ($1::text IS NULL OR m2.account_email = $1)
                 ORDER BY m2.internal_date DESC LIMIT 1) cls ON true
              LEFT JOIN LATERAL (
                SELECT m3.payload->'headers'->>'list-unsubscribe' AS list_unsubscribe,
                       m3.payload->'headers'->>'list-unsubscribe-post' AS list_unsubscribe_post
                  FROM raw.gmail_message m3
                 WHERE m3.from_address = s.from_address
                   AND ($1::text IS NULL OR m3.account_email = $1)
                   AND m3.payload->'headers' ? 'list-unsubscribe'
                 ORDER BY m3.internal_date DESC LIMIT 1) lu ON true
             ORDER BY s.messages DESC, s.last_at DESC
            """, account)
    out = []
    for r in rows:
        d = dict(r)
        lu = d.pop("list_unsubscribe", None)
        d["unsubscribe_url"] = _unsubscribe_https(lu) or _unsubscribe_mailto(lu)
        d["one_click"] = _unsubscribe_one_click(lu, d.pop("list_unsubscribe_post", None))
        d["on_clear_list"] = True
        out.append(d)
    return out


async def set_mail_state(pool: asyncpg.Pool, *, account_email: str, thread_key: str,
                         archived: bool | None = None, starred: bool | None = None,
                         read: bool | None = None, trashed: bool | None = None,
                         snoozed_until=None, set_snooze: bool = False) -> dict:
    """Upsert a thread's local archive/star/read/trash/snooze state (only the
    provided fields). snoozed_until only changes when set_snooze is True — an
    explicit NULL with set_snooze unsnoozes; without it the value is untouched."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO memory.mail_state
                   (account_email, thread_key, archived, starred, read, trashed, snoozed_until, updated_at)
            VALUES ($1, $2, COALESCE($3, false), COALESCE($4, false), COALESCE($5, false),
                    COALESCE($6, false), CASE WHEN $8 THEN $7::timestamptz END, now())
            ON CONFLICT (account_email, thread_key) DO UPDATE
              SET archived = COALESCE($3, memory.mail_state.archived),
                  starred  = COALESCE($4, memory.mail_state.starred),
                  read     = COALESCE($5, memory.mail_state.read),
                  trashed  = COALESCE($6, memory.mail_state.trashed),
                  snoozed_until = CASE WHEN $8 THEN $7::timestamptz ELSE memory.mail_state.snoozed_until END,
                  updated_at = now()
            RETURNING account_email, thread_key, archived, starred, read, trashed, snoozed_until
            """, account_email, thread_key, archived, starred, read, trashed,
            snoozed_until, set_snooze)
    return dict(row)


async def mark_all_mail_read(pool: asyncpg.Pool, *, account: str | None = None) -> int:
    """Mark every currently-unread thread read (app-local), as of now. New mail that
    arrives later stays unread. Returns the number of threads marked."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            INSERT INTO memory.mail_state (account_email, thread_key, read, updated_at)
            SELECT DISTINCT account_email, COALESCE(thread_id, message_id), true, now()
              FROM raw.gmail_message
             WHERE 'UNREAD' = ANY(labels)
               AND ($1::text IS NULL OR account_email = $1)
            ON CONFLICT (account_email, thread_key) DO UPDATE
              SET read = true, updated_at = now()
            """, account)
    # asyncpg returns e.g. "INSERT 0 42"; the last token is the affected row count.
    try:
        return int(result.split()[-1])
    except (ValueError, IndexError):
        return 0


async def get_mail_thread(pool: asyncpg.Pool, thread_key: str,
                          account: str | None = None) -> list[dict]:
    """All messages in a thread, oldest → newest (full bodies).

    `attachments`/`headers` are the per-message metadata stored at ingest
    (payload->'attachments'/'headers'); `_attachments_known`/`_headers_known` flag
    whether the payload carried each key — messages ingested before Phase 3b/B have
    not, so the caller can lazily backfill them. `signals` is the header-heuristic
    triage verdict. The `_*_known` flags are stripped before serialization."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT m.message_id, m.thread_id, m.rfc822_message_id, m.account_email,
                   m.from_address, m.from_name, m.to_addresses, m.cc_addresses, m.subject,
                   m.body_text, m.body_html, m.internal_date, m.labels,
                   COALESCE(m.payload->'attachments', '[]'::jsonb) AS attachments,
                   m.payload->'headers' AS headers,
                   (m.payload ? 'attachments') AS _attachments_known,
                   (m.payload ? 'headers') AS _headers_known,
                   sp.score AS spam_score, sp.action AS spam_action,
                   mc.content_class, mc.confidence AS content_confidence
              FROM raw.gmail_message m
              LEFT JOIN memory.mail_spam sp
                ON sp.account_email = m.account_email AND sp.message_id = m.message_id
              LEFT JOIN memory.mail_class mc
                ON mc.account_email = m.account_email AND mc.message_id = m.message_id
             WHERE COALESCE(m.thread_id, m.message_id) = $1
               AND ($2::text IS NULL OR m.account_email = $2)
             ORDER BY m.internal_date ASC
            """, thread_key, account)
    out = []
    for r in rows:
        d = dict(r)
        att = d.get("attachments")
        d["attachments"] = json.loads(att) if isinstance(att, str) else (att or [])
        hdrs = _parse_headers(d.pop("headers", None))
        d["signals"] = triage_signals(hdrs)
        d["is_spam"] = _mail_is_spam(d.get("labels"), d.get("spam_action"), hdrs)
        out.append(d)
    return out


async def list_unheadered_messages(pool: asyncpg.Pool, *, account: str | None = None,
                                   limit: int = 500) -> list[dict]:
    """Messages ingested before Phase B (no payload->'headers'), newest first, for a
    bulk header backfill. Shaped for gmail_fetch.backfill_thread_metadata.

    Restricted to accounts with an ACTIVE OAuth token — import-only accounts have no
    token to fetch format=raw/full, so their mail can never be backfilled and would
    otherwise clog the loop forever (backfilled=0)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT m.account_email, m.message_id,
                   (m.payload ? 'attachments') AS _attachments_known,
                   (m.payload ? 'headers') AS _headers_known
              FROM raw.gmail_message m
              JOIN raw.gmail_account a
                ON a.email = m.account_email AND a.status = 'active'
                   AND a.refresh_token IS NOT NULL
             WHERE NOT (m.payload ? 'headers')
               AND ($1::text IS NULL OR m.account_email = $1)
             ORDER BY m.internal_date DESC
             LIMIT $2
            """, account, max(1, min(limit, 2000)))
    return [dict(r) for r in rows]


async def upsert_mail_spam(pool: asyncpg.Pool, *, account_email: str, message_id: str,
                           score: float | None, action: str | None, symbols: dict) -> None:
    """Persist a Rspamd verdict for one message (Phase B2)."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO memory.mail_spam
              (account_email, message_id, score, action, symbols, checked_at)
            VALUES ($1, $2, $3, $4, $5::jsonb, now())
            ON CONFLICT (account_email, message_id) DO UPDATE
              SET score = EXCLUDED.score, action = EXCLUDED.action,
                  symbols = EXCLUDED.symbols, checked_at = now()
            """, account_email, message_id, score, action, json.dumps(symbols or {}))


async def list_unscored_messages(pool: asyncpg.Pool, *, account: str | None = None,
                                 limit: int = 200) -> list[dict]:
    """Recent messages with no Rspamd verdict yet (newest first), for a batch scan.
    Skips already-scored messages so repeat scans only cover new mail."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT m.account_email, m.message_id
              FROM raw.gmail_message m
             WHERE ($1::text IS NULL OR m.account_email = $1)
               AND NOT EXISTS (
                     SELECT 1 FROM memory.mail_spam sp
                      WHERE sp.account_email = m.account_email
                        AND sp.message_id = m.message_id)
             ORDER BY m.internal_date DESC
             LIMIT $2
            """, account, max(1, min(limit, 1000)))
    return [dict(r) for r in rows]


# --- Rspamd Bayes training corpus (triage iteration) ---------------------------

async def list_active_mail_accounts(pool: asyncpg.Pool, *, account: str | None = None) -> list[str]:
    """Gmail accounts usable for API calls (active + token). Optional narrowing."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT email FROM raw.gmail_account
             WHERE status = 'active' AND refresh_token IS NOT NULL
               AND ($1::text IS NULL OR email = $1)
             ORDER BY email
            """, account)
    return [r["email"] for r in rows]

async def list_bayes_ham_candidates(pool: asyncpg.Pool, *, account: str | None = None,
                                    limit: int = 100) -> list[dict]:
    """Ham corpus for /learnham: messages Rspamd already scored clean
    ('no action'/'greylist'), not Gmail-SPAM-labeled, not yet learned. Highest-
    precision ham first: personal-classified, then read mail, newest first.
    (The spam corpus is NOT a DB query — it's listed live from Gmail's spam
    folder via gmail_fetch.list_spam_message_ids; spam never gets ingested.)"""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT m.account_email, m.message_id
              FROM raw.gmail_message m
              JOIN memory.mail_spam sp
                ON sp.account_email = m.account_email AND sp.message_id = m.message_id
              LEFT JOIN memory.mail_class mc
                ON mc.account_email = m.account_email AND mc.message_id = m.message_id
             WHERE ($1::text IS NULL OR m.account_email = $1)
               AND sp.action IN ('no action', 'greylist')
               AND NOT ('SPAM' = ANY(m.labels))
               AND NOT EXISTS (
                     SELECT 1 FROM memory.mail_bayes_learn bl
                      WHERE bl.account_email = m.account_email
                        AND bl.message_id = m.message_id)
             ORDER BY (mc.content_class = 'personal') DESC NULLS LAST,
                      ('UNREAD' = ANY(m.labels)) ASC,
                      m.internal_date DESC
             LIMIT $2
            """, account, max(1, min(limit, 1000)))
    return [dict(r) for r in rows]


async def filter_unlearned(pool: asyncpg.Pool, *, account_email: str,
                           message_ids: list[str]) -> list[str]:
    """Subset of message_ids not yet in the Bayes learn ledger (order kept).
    Used on the live-listed Gmail spam ids, which have no raw.gmail_message rows."""
    if not message_ids:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT message_id FROM memory.mail_bayes_learn
             WHERE account_email = $1 AND message_id = ANY($2::text[])
            """, account_email, message_ids)
    learned = {r["message_id"] for r in rows}
    return [m for m in message_ids if m not in learned]


async def count_bayes_learned(pool: asyncpg.Pool) -> dict[str, int]:
    """Cumulative learns per class — feeds the batch planner's imbalance cap
    and shows min_learns progress in the timer log."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT learned_as, count(*)::int AS n FROM memory.mail_bayes_learn GROUP BY learned_as")
    out = {"spam": 0, "ham": 0}
    for r in rows:
        out[r["learned_as"]] = r["n"]
    return out


async def record_bayes_learn(pool: asyncpg.Pool, *, account_email: str,
                             message_id: str, learned_as: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO memory.mail_bayes_learn (account_email, message_id, learned_as)
            VALUES ($1, $2, $3) ON CONFLICT DO NOTHING
            """, account_email, message_id, learned_as)


async def set_mail_class_user(pool: asyncpg.Pool, *, account_email: str,
                              thread_key: str, content_class: str) -> int:
    """User correction: stamp every message in the thread with the given class
    as ground truth (confidence 1.0, model_version='user'). The classifier and
    refine tier never overwrite 'user' rows. Returns messages stamped."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            INSERT INTO memory.mail_class
                   (account_email, message_id, content_class, confidence, model_version, classified_at)
            SELECT m.account_email, m.message_id, $3, 1.0, 'user', now()
              FROM raw.gmail_message m
             WHERE m.account_email = $1
               AND COALESCE(m.thread_id, m.message_id) = $2
            ON CONFLICT (account_email, message_id) DO UPDATE
              SET content_class = EXCLUDED.content_class, confidence = 1.0,
                  model_version = 'user', classified_at = now()
            """, account_email, thread_key, content_class)
    try:
        return int(result.split()[-1])
    except (ValueError, IndexError):
        return 0
