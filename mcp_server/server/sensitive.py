"""The one rule for contacts marked `sensitive`: their data stays on the box.

`canonical.person.sensitive` routes a contact to the local model only. Every
tool on this server is called by a cloud model, so each query that returns
people, messages or mail carries one of these fragments, and the by-id tools
answer with `redacted()` instead of the row. `local_ask` is the one exception:
it never leaves the droplet. Same contract merge_api applies on its side.

Strings and two tiny helpers only, so a test can prove the guard is present
without a database.
"""
from __future__ import annotations

from typing import Any

NOTE = "sensitive contact — details only through local_ask"

# Uncorrelated, so Postgres hashes it once per query whatever the row count.
SENSITIVE_IDS = "(SELECT id FROM canonical.person WHERE sensitive)"


def person_clause(alias: str = "p") -> str:
    """For a query that already has canonical.person in scope as `alias`."""
    return f"COALESCE({alias}.sensitive, false) = false"


# canonical.interaction as `i`: hide a message when either side is sensitive.
# COALESCE because `x IN (...)` is NULL, not false, when x is NULL — a bare
# NOT IN would silently drop every row without a counterparty.
INTERACTION_CLAUSE = (
    f"NOT (COALESCE(i.person_id IN {SENSITIVE_IDS}, false)"
    f" OR COALESCE(i.author_person_id IN {SENSITIVE_IDS}, false))"
)

# raw.gmail_message as `m`: hide a mail when any address on it is a sensitive
# contact's email identity (the same identity sources merge_api reads).
SENSITIVE_EMAILS = (
    "(SELECT lower(si.source_id) FROM canonical.identity si"
    " WHERE si.source IN ('email', 'gmail')"
    f" AND si.person_id IN {SENSITIVE_IDS})"
)
MAIL_CLAUSE = (
    f"NOT (COALESCE(lower(m.from_address) IN {SENSITIVE_EMAILS}, false)"
    " OR EXISTS (SELECT 1 FROM unnest(COALESCE(m.to_addresses, ARRAY[]::text[])"
    " || COALESCE(m.cc_addresses, ARRAY[]::text[])) AS a"
    f" WHERE lower(a) IN {SENSITIVE_EMAILS}))"
)


def redacted(person_id: str) -> dict[str, Any]:
    """What a by-id tool returns for a sensitive contact: the id it was asked
    about and where to go instead — nothing from the row."""
    return {"person_id": person_id, "note": NOTE}


async def is_sensitive(conn, person_id: str) -> bool:
    row = await conn.fetchrow(
        "SELECT COALESCE(sensitive, false) AS s FROM canonical.person WHERE id = $1::uuid",
        person_id,
    )
    return bool(row and row["s"])
