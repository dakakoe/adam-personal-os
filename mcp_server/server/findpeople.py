"""find_people — "who can help me with X?", ranked by role card.

Every rule here was measured against the user's circles (eval/find_people_eval):
the one-line role card ranks twice as many circle members into the top 50 as
the profile paragraph, and every blend with the paragraph or with message
similarity did worse. So:

- members of circles the CALLER names come first. The caller is an LLM and
  maps a need to circles reliably; embeddings could not (~0.01 margins, one
  marketing circle in the top four for 7 of 10 needs);
- everyone else by card similarity, among people the user knows unless
  scope="all";
- no relationship lift: ranking whoever you are closest to higher, by any
  margin, cost recall (27 -> 26 at 0.01, 20 at 0.02) — closeness is not
  expertise. Each result lists its circles, and the caller weighs it itself;
- members of the configured excluded circles never appear — any scope, any
  circles named (FIND_PEOPLE_EXCLUDED_CIRCLES; which circle that is is the
  user's business, so it is never a literal here);
- messages are evidence of why someone fits, never a ranking signal.

The SQL lives here so the harness scores exactly what the tool returns.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence

SCOPES = ("known", "all")
DEFAULT_LIMIT = 15
MAX_LIMIT = 50
EVIDENCE_PER_PERSON = 2
SNIPPET_CHARS = 220

CATALOG_SQL = """
SELECT c.key, c.label, count(*) AS members
FROM memory.contact_circle c
JOIN memory.contact_circle_member m ON m.circle_id = c.id
WHERE NOT (c.key = ANY($1::text[]))
GROUP BY c.id, c.key, c.label
ORDER BY c.label
"""

RESOLVE_SQL = """
SELECT key, label FROM memory.contact_circle
WHERE (lower(key) = ANY($1::text[]) OR lower(label) = ANY($1::text[]))
  AND NOT (key = ANY($2::text[]))
ORDER BY label
"""

# $1 query vector, $2 limit, $3 requested circle keys, $4 excluded circle keys,
# $5 include strangers.
# MATERIALIZED scores every candidate exactly before sorting: an HNSW scan
# under these joins and filters would silently return fewer rows.
RANK_SQL = """
WITH requested AS (
  SELECT DISTINCT m.person_id FROM memory.contact_circle_member m
  JOIN memory.contact_circle c ON c.id = m.circle_id WHERE c.key = ANY($3::text[])
), barred AS (
  SELECT DISTINCT m.person_id FROM memory.contact_circle_member m
  JOIN memory.contact_circle c ON c.id = m.circle_id WHERE c.key = ANY($4::text[])
), known AS (
  SELECT DISTINCT person_id FROM canonical.interaction
  WHERE person_id IS NOT NULL AND (direction = 'outbound' OR channel = 'meeting')
), scored AS MATERIALIZED (
  SELECT p.id AS person_id,
         mp.card_embedding <=> $1 AS distance,
         r.person_id IS NOT NULL AS in_requested,
         k.person_id IS NOT NULL AS known
  FROM canonical.person p
  LEFT JOIN memory.profile mp ON mp.person_id = p.id
  LEFT JOIN requested r ON r.person_id = p.id
  LEFT JOIN known k ON k.person_id = p.id
  WHERE p.merged_into IS NULL
    -- A named circle is a direct lookup: its members come back even with no
    -- card, and even when the user never wrote to them.
    AND (mp.card_embedding IS NOT NULL OR r.person_id IS NOT NULL)
    AND (r.person_id IS NOT NULL OR k.person_id IS NOT NULL OR $5::bool)
    AND NOT EXISTS (SELECT 1 FROM barred b WHERE b.person_id = p.id)
), top AS (
  SELECT s.*, row_number() OVER (
           ORDER BY s.in_requested DESC,
                    s.distance ASC NULLS LAST,
                    s.person_id) AS rn
  FROM scored s
  ORDER BY rn
  LIMIT $2
)
SELECT t.person_id::text AS person_id, t.rn, p.display_name, p.sensitive,
       mp.card_text, t.distance, t.in_requested, t.known,
       (SELECT array_agg(c.label ORDER BY c.priority, c.label)
          FROM memory.contact_circle_member m
          JOIN memory.contact_circle c ON c.id = m.circle_id
         WHERE m.person_id = t.person_id) AS circles,
       (SELECT max(i.occurred_at) FROM canonical.interaction i
         WHERE i.person_id = t.person_id) AS last_contact
FROM top t
JOIN canonical.person p ON p.id = t.person_id
LEFT JOIN memory.profile mp ON mp.person_id = t.person_id
ORDER BY t.rn
"""

# The closest messages to the need, per person — DMs, mail, meetings, and what
# they said in groups. Mail is evidence only once the classifier has called it
# personal: bulk never is, and neither is mail not yet scored (the classifier
# runs every 30 minutes, HTML-only mail included).
EVIDENCE_SQL = """
WITH theirs AS MATERIALIZED (
  SELECT i.id, COALESCE(i.person_id, i.author_person_id) AS pid, i.occurred_at,
         i.channel, i.direction, i.body, i.raw_source, i.raw_id,
         e.embedding <=> $1 AS distance
  FROM canonical.interaction i
  JOIN memory.interaction_embedding e ON e.interaction_id = i.id
  WHERE (i.person_id = ANY($2::uuid[]) OR i.author_person_id = ANY($2::uuid[]))
    -- One-word replies ("yes", "да") embed close to every query and explain
    -- nothing.
    AND i.body IS NOT NULL AND length(i.body) >= 20
), clean AS (
  SELECT t.*, row_number() OVER (PARTITION BY t.pid ORDER BY t.distance, t.id) AS rk
  FROM theirs t
  LEFT JOIN raw.gmail_message gm ON t.raw_source = 'raw.gmail_message' AND gm.id = t.raw_id
  LEFT JOIN memory.mail_class mc
    ON mc.account_email = gm.account_email AND mc.message_id = gm.message_id
  WHERE t.raw_source IS DISTINCT FROM 'raw.gmail_message'
     OR mc.content_class = 'personal'
)
SELECT pid::text AS person_id, occurred_at, channel, direction, body, distance
FROM clean WHERE rk <= $3
ORDER BY pid, rk
"""


def normalize_circles(circles: Iterable[str] | str | None) -> list[str]:
    """Lower-cased, trimmed, de-duplicated, order kept. Accepts a comma string
    too, since callers sometimes send one."""
    if not circles:
        return []
    if isinstance(circles, str):
        circles = circles.split(",")
    out: list[str] = []
    for c in circles:
        c = (c or "").strip().lower()
        if c and c not in out:
            out.append(c)
    return out


def unmatched(requested: Sequence[str], matched: Iterable[Mapping[str, Any]]) -> list[str]:
    """Requested names that matched no circle by key or label."""
    known = set()
    for m in matched:
        known.add(m["key"].lower())
        known.add(m["label"].lower())
    return [r for r in requested if r not in known]


def relationship(known: bool, last_contact: Any) -> str:
    if known:
        return "known"
    if last_contact is not None:
        return "wrote to you, never answered"
    return "never talked"


def snippet(body: str | None, n: int = SNIPPET_CHARS) -> str:
    text = re.sub(r"\s+", " ", body or "").strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _day(ts: Any) -> str | None:
    return ts.date().isoformat() if ts is not None and hasattr(ts, "date") else None


def shape(row: Mapping[str, Any], evidence: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """One result. Sensitive contacts come back name-only: the caller may be
    cloud-hosted, and nothing drawn from their messages leaves this server."""
    out: dict[str, Any] = {
        "person_id": row["person_id"],
        "name": row["display_name"],
        "in_requested_circle": bool(row["in_requested"]),
    }
    if row["sensitive"]:
        out["note"] = "sensitive contact — details only through local_ask"
        return out
    distance = row["distance"]
    out.update({
        "card": row["card_text"],
        "circles": list(row["circles"] or []),
        "relationship": relationship(bool(row["known"]), row["last_contact"]),
        "last_contact": _day(row["last_contact"]),
        "distance": round(float(distance), 4) if distance is not None else None,
        "evidence": [
            {"date": _day(e["occurred_at"]), "channel": e["channel"],
             "direction": e["direction"], "snippet": snippet(e["body"]),
             "distance": round(float(e["distance"]), 4)}
            for e in evidence
        ],
    })
    return out


def describe(catalog: Sequence[Mapping[str, Any]]) -> str:
    """The tool description, with the user's circles baked in at startup so the
    caller can pick circles without a second round trip."""
    lines = [
        "Find people who can help with a need — 'who can help me with X?'.",
        "",
        "Pass `circles`: the user's circles that fit the need, by key or label, "
        "from the list below. Their members come first. Choose by what people "
        "ARE (e.g. Investors for a raise, Lawyers for licensing) — leave out "
        "circles that only describe closeness. Everyone else is ranked by a "
        "one-line role card.",
        "",
        "`scope`: 'known' (default) — people the user has met or written to; "
        "'all' adds people who only wrote in or are known only from LinkedIn, "
        "marked by `relationship`.",
        "",
        "Each result: name, role card, circles, relationship, last contact, and "
        "up to two message snippets as evidence. The ranking is a recall aid, "
        "not a verdict — judge fit from the card and circles, and drop "
        "mismatches. When fit is equal, prefer people whose circles show "
        "closeness (e.g. Worked Together, Important, VIP) — the ranking "
        "deliberately ignores it. Snippets are the closest messages, not "
        "proof, and can be off-topic. Some people are never returned, by the "
        "user's rule. "
        "Sensitive contacts come back name-only; use local_ask for them.",
    ]
    if catalog:
        lines += ["", "Circles (members): " + ", ".join(
            f"{c['label']} ({c['members']})" for c in catalog)]
    return "\n".join(lines)


async def circle_catalog(conn, excluded: Sequence[str]) -> list[Mapping[str, Any]]:
    return await conn.fetch(CATALOG_SQL, list(excluded))


async def resolve_circles(conn, requested: Sequence[str],
                          excluded: Sequence[str]) -> list[Mapping[str, Any]]:
    if not requested:
        return []
    return await conn.fetch(RESOLVE_SQL, list(requested), list(excluded))


async def rank(conn, qvec, *, circle_keys: Sequence[str], include_strangers: bool,
               limit: int, excluded: Sequence[str] = ()) -> list[Mapping[str, Any]]:
    return await conn.fetch(
        RANK_SQL, qvec, limit, list(circle_keys), list(excluded),
        include_strangers,
    )


async def evidence(conn, qvec, person_ids: Sequence[str],
                   per_person: int = EVIDENCE_PER_PERSON) -> dict[str, list[Mapping[str, Any]]]:
    if not person_ids:
        return {}
    out: dict[str, list[Mapping[str, Any]]] = {}
    for r in await conn.fetch(EVIDENCE_SQL, qvec, list(person_ids), per_person):
        out.setdefault(r["person_id"], []).append(r)
    return out
