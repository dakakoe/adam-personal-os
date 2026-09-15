"""Send every SQL statement in queries.py to Postgres and make it parse.

The gap this closes: nothing in the suite ever executed these strings. A SQL
constant was just text until production parsed it, so `) both` — a reserved
word as a subquery alias — passed 211 green tests and 500'd every follow-up
request. Undefined columns, dropped tables and type mismatches are all the
same shape of bug: a change looks right, ships, and fails at parse time.

PREPARE is the whole trick. Postgres does the full parse-and-plan and tells us
about anything wrong with the statement, but never runs it — so INSERTs and
DELETEs are checked exactly as safely as SELECTs, and the database is left
untouched. No fixtures, no test data, no cleanup.

Run it: scripts/sql-smoke.sh (builds a scratch schema-only copy of production
and points this at it). Without SMOKE_DATABASE_URL the whole module skips, so
the ordinary offline suite is unaffected.
"""

from __future__ import annotations

import ast
import asyncio
import os
import re
from pathlib import Path

import pytest

try:
    import asyncpg
except ModuleNotFoundError:  # pragma: no cover - offline environments
    asyncpg = None

from merge_api import queries

DSN = os.environ.get("SMOKE_DATABASE_URL")
QUERIES_PY = Path(queries.__file__)

pytestmark = pytest.mark.skipif(
    not DSN or asyncpg is None,
    reason="set SMOKE_DATABASE_URL (see scripts/sql-smoke.sh) to run the SQL smoke test",
)

# `/*MATCH*/` picks which follow-up a request is about. Both branches ship, so
# both get checked.
MATCH_FRAGMENTS = [
    ("by-person", queries._FOLLOWUP_BY_PERSON),
    ("by-id", queries._FOLLOWUP_BY_ID),
]

# `/*VIS*/` scopes a query to what its viewer may see. The fragment is built by
# the real visibility_clause(), never re-implemented here — a copy would drift
# from the thing it's meant to check. What the test does have to supply is what
# the call site knows: which table alias is being scoped, and how many binds
# precede the member id (visibility_clause appends its own, positionally).
#
# Guessing the alias by regex was tried and was worse than useless: it matched
# `FROM canonical.person\n WHERE` and produced `WHERE.visibility`. An explicit
# map can go stale, but staleness fails this test loudly, which is the point.
VIS_CALL_SITES = {
    "COUNT_PERSONS_SQL": ("p", 3),        # q, company_id, circle
    "LIST_PERSONS_SQL": ("p", 5),         # + limit, offset
    "COMPANY_LIST_MEMBER_SQL": ("c", 3),  # q, limit, offset
}

# Fragments, not statements: pasted into a larger query, not valid alone. Each
# is exercised through whatever composes it.
NOT_STANDALONE = {
    "_FOLLOWUP_BY_PERSON",
    "_FOLLOWUP_BY_ID",
    # concatenated into LIST_/COUNT_CLEANUP_CANDIDATES_SQL, both checked
    "CLEANUP_CANDIDATES_BASE_SQL",
}


def _module_sql() -> list[tuple[str, str]]:
    """(label, sql) for every module-level SQL constant."""
    return [
        (name, value)
        for name, value in vars(queries).items()
        if name.endswith("_SQL")
        and isinstance(value, str)
        and name not in NOT_STANDALONE
        and re.search(r"\b(SELECT|INSERT|UPDATE|DELETE|WITH)\b", value, re.IGNORECASE)
    ]


def _inline_sql() -> list[tuple[str, str]]:
    """(label, sql) for SQL written straight into a conn.fetch/execute call.

    Plenty of statements never become a named constant. They break the same
    way, so they're checked the same way. Only literal strings are collected —
    anything built at runtime is out of reach here.
    """
    tree = ast.parse(QUERIES_PY.read_text())
    found: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in {"fetch", "fetchrow", "fetchval", "execute", "executemany"}:
            continue
        if not node.args:
            continue
        arg = node.args[0]
        # A literal, or adjacent literals the parser already folded into one.
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            sql = arg.value
        else:
            continue
        if not re.search(r"\b(SELECT|INSERT|UPDATE|DELETE|WITH)\b", sql, re.IGNORECASE):
            continue
        found.append((f"queries.py:{node.lineno}", sql))
    return found


def _vis_options(label: str) -> list[tuple[str, str]]:
    """The two shapes a /*VIS*/ query ships in: unscoped for a viewer who sees
    everything, and the member predicate for one who doesn't."""
    alias, preceding = VIS_CALL_SITES[label]
    params: list = [None] * preceding
    member = queries.visibility_clause(alias, {"member_id": None, "role": "budget"}, params)
    return [("owner", ""), ("member", member)]


def _expand(label: str, sql: str) -> list[tuple[str, str]]:
    """One statement per placeholder combination."""
    out = [(label, sql)]
    for token, options in (("/*MATCH*/", MATCH_FRAGMENTS),
                           ("/*VIS*/", _vis_options(label) if label in VIS_CALL_SITES else [])):
        if not any(token in s for _, s in out) or not options:
            continue
        out = [
            (f"{lbl} [{suffix}]", body.replace(token, fragment))
            for lbl, body in out
            for suffix, fragment in options
        ]
    return out


def _statements() -> list[tuple[str, str]]:
    stmts: list[tuple[str, str]] = []
    for label, sql in _module_sql() + _inline_sql():
        stmts.extend(_expand(label, sql))
    return stmts


ALL_STATEMENTS = _statements()


def test_statements_were_found():
    """A collector that silently finds nothing would make every check below
    pass. Pin a floor well under the current count."""
    assert len(ALL_STATEMENTS) > 40, f"only collected {len(ALL_STATEMENTS)} statements"


def test_no_unexpanded_placeholders():
    """A new /*TOKEN*/ must be taught to PLACEHOLDERS — otherwise its query
    quietly stops being checked at all."""
    leftover = [
        f"{label}: {m.group(0)}"
        for label, sql in ALL_STATEMENTS
        if (m := re.search(r"/\*[A-Z_]+\*/", sql))
    ]
    assert not leftover, "unexpanded SQL placeholders:\n  " + "\n  ".join(leftover)


def test_every_statement_parses():
    async def run() -> list[str]:
        conn = await asyncpg.connect(DSN)
        failures = []
        try:
            for label, sql in ALL_STATEMENTS:
                try:
                    await conn.prepare(sql)
                except asyncpg.exceptions.IndeterminateDatatypeError:
                    # "could not determine data type of parameter $n" — the
                    # statement parsed and every table, column and function
                    # resolved; only an uncast bind is ambiguous, which the
                    # real call settles by passing a typed value. Not a defect.
                    pass
                except asyncpg.PostgresSyntaxError as e:
                    failures.append(f"{label}: SYNTAX {e}")
                except asyncpg.PostgresError as e:
                    failures.append(f"{label}: {type(e).__name__} {e}")
        finally:
            await conn.close()
        return failures

    failures = asyncio.run(run())
    assert not failures, (
        f"{len(failures)} of {len(ALL_STATEMENTS)} statements failed to parse:\n  "
        + "\n  ".join(failures)
    )
