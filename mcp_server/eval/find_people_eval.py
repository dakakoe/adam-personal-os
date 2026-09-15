"""How many of your circle members does each ranking put in its top 10/25/50,
for your "who can help with X" needs? Gap 3's ship gate.

The answer key is computed live from memory.contact_circle_member, so no file
of names exists and every circle you fill extends the key. The needs come from
FIND_PEOPLE_EVAL_NEEDS (see SAMPLE_NEEDS). Output is counts only. Read-only
against the database.

On the droplet:

    cd /srv/memory/apps/mcp_server && set -a && . /srv/memory/secrets/.env && set +a
    .venv/bin/python -m eval.find_people_eval --save /srv/memory/data/find-people-baseline.json
    .venv/bin/python -m eval.find_people_eval --compare /srv/memory/data/find-people-baseline.json

--compare exits 1 when any arm finds fewer circle members than the saved run.
A change that does that does not ship.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone

import asyncpg
import pgvector.asyncpg

from server import config, embed, findpeople
from server.recall import KS, answer_key, hits_at, regressions
from server.tools import SEMANTIC_PEOPLE_SQL

# (need, circle keys that answer it). An empty tuple means no circle answers it
# yet: it is printed so the gap stays visible, and scored as soon as one exists.
#
# A SAMPLE, so the harness runs out of the box. Your real needs belong in a JSON
# file named by FIND_PEOPLE_EVAL_NEEDS, not in the repo — what you ask for says
# what you are working on:
#     [{"need": "investors for a round", "circles": ["investors"]}, ...]
SAMPLE_NEEDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("investors for a round", ("investors",)),
    ("a lawyer for company setup and licensing", ("lawyers",)),
)
DEPTH = max(KS)


def load_needs(path: str | None) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """The need -> circles map: yours when configured, else the sample."""
    if not path:
        return SAMPLE_NEEDS
    with open(path) as fh:
        return tuple((r["need"], tuple(r.get("circles") or ())) for r in json.load(fh))

MEMBERS_SQL = """
SELECT c.key, m.person_id::text AS person_id
FROM memory.contact_circle c
JOIN memory.contact_circle_member m ON m.circle_id = c.id
"""

# People you met or wrote to. MATERIALIZED computes every distance before the
# sort, so an HNSW index cannot return fewer than DEPTH rows after the join.
def _known_ranking(vector_column: str) -> str:
    return f"""
WITH known AS (
  SELECT DISTINCT person_id FROM canonical.interaction
  WHERE person_id IS NOT NULL AND (direction = 'outbound' OR channel = 'meeting')
), scored AS MATERIALIZED (
  SELECT mp.person_id, mp.{vector_column} <=> $1 AS distance
  FROM known k
  JOIN memory.profile mp ON mp.person_id = k.person_id
  JOIN canonical.person p ON p.id = mp.person_id
  WHERE mp.{vector_column} IS NOT NULL AND p.merged_into IS NULL
)
SELECT person_id::text AS person_id FROM scored ORDER BY distance LIMIT $2
"""


def _sql_arm(sql: str):
    async def arm(conn, qvec, depth: int) -> list[str]:
        return [r["person_id"] for r in await conn.fetch(sql, qvec, depth)]
    return arm


def _find_people_arm(excluded: tuple[str, ...]):
    async def arm(conn, qvec, depth: int) -> list[str]:
        # No circles named: the circle lookup is correct by construction, so
        # what is scored is the card ranking that serves everyone in no circle.
        rows = await findpeople.rank(conn, qvec, circle_keys=[], excluded=excluded,
                                     include_strangers=False, limit=depth)
        return [r["person_id"] for r in rows]
    return arm


# arm name -> async (conn, query vector, depth) -> person_ids in rank order.
def build_arms(excluded: tuple[str, ...]) -> dict:
    return {
        "today": _sql_arm(SEMANTIC_PEOPLE_SQL),   # semantic_search_people verbatim
        "known": _sql_arm(_known_ranking("embedding")),
        "card": _sql_arm(_known_ranking("card_embedding")),
        # The shipped tool: card ranking plus the configured exclusions.
        "find_people": _find_people_arm(excluded),
    }


async def run() -> dict:
    cfg = config.load()
    needs = load_needs(cfg.eval_needs_path)
    arms = build_arms(cfg.excluded_circles)
    conn = await asyncpg.connect(cfg.db_url)
    await pgvector.asyncpg.register_vector(conn)
    try:
        members: dict[str, set[str]] = {}
        for r in await conn.fetch(MEMBERS_SQL):
            members.setdefault(r["key"], set()).add(r["person_id"])

        totals = {arm: [0] * len(KS) for arm in arms}
        key_total = 0
        width = 34
        print(f"{'#':>2} {'need':<{width}}{'key':>5}  " + "".join(f"{a:>14}" for a in arms))
        for i, (need, circles) in enumerate(needs, start=1):
            key = answer_key(circles, members, cfg.excluded_circles)
            if not key:
                print(f"{i:>2} {need[:width]:<{width}}{'—':>5}  no circle answers this yet")
                continue
            qvec = await embed.encode_query(need)
            row = []
            for arm, fetch in arms.items():
                ranked = await fetch(conn, qvec, DEPTH)
                hits = hits_at(ranked, key)
                totals[arm] = [t + h for t, h in zip(totals[arm], hits)]
                row.append("/".join(map(str, hits)))
            key_total += len(key)
            print(f"{i:>2} {need[:width]:<{width}}{len(key):>5}  " + "".join(f"{c:>14}" for c in row))
        print(f"{'':>2} {'TOTAL @' + '/'.join(map(str, KS)):<{width}}{key_total:>5}  "
              + "".join(f"{'/'.join(map(str, totals[a])):>14}" for a in arms))
    finally:
        await conn.close()
    return {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "key_size": key_total,
        "needs": [n for n, _ in needs],
        "arms": totals,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--save", metavar="PATH", help="write this run's counts as the new baseline")
    ap.add_argument("--compare", metavar="PATH", help="exit 1 if any arm lost recall versus PATH")
    args = ap.parse_args()

    result = asyncio.run(run())
    status = 0
    if args.compare:
        with open(args.compare) as fh:
            base = json.load(fh)
        if base.get("needs") != result["needs"]:
            print("note: the needs changed since the baseline; totals are not comparable")
        elif base.get("key_size") != result["key_size"]:
            print(f"note: the answer key changed ({base.get('key_size')} -> {result['key_size']}),"
                  " circles were edited since the baseline")
        lost = regressions(result["arms"], base["arms"])
        for line in lost:
            print(f"REGRESSION {line}")
        status = 1 if lost else 0
        if not lost:
            print(f"no regressions against {args.compare} ({base.get('at')})")
    if args.save:
        with open(args.save, "w") as fh:
            json.dump(result, fh, indent=2)
        print(f"saved baseline to {args.save}")
    return status


if __name__ == "__main__":
    sys.exit(main())
