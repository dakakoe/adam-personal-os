"""Subquery aliases in our SQL must not be Postgres reserved words.

`) both` shipped to production and 500'd every follow-up request: `both` is
reserved (TRIM(BOTH … )), so it is a syntax error as an alias. Nothing caught
it — the whole suite runs without a database, so a SQL string is just a string
until Postgres parses it, and by then it's live.

This is a static net for exactly that class of mistake. It does NOT make the
SQL correct; it only guarantees the aliases we invent are legal identifiers.
"""

from __future__ import annotations

import re
from pathlib import Path

QUERIES = Path(__file__).resolve().parents[1] / "merge_api" / "queries.py"

# Fully reserved in Postgres: illegal as a bare alias. Trimmed to words that
# could plausibly be *chosen* as an alias — a name for a set of rows. Words
# like SELECT/WHERE/UNION are omitted deliberately: nobody aliases a subquery
# "where", and they appear constantly as legitimate `)` followers.
RESERVED_ALIASES = {
    "all", "analyse", "analyze", "any", "array", "asymmetric", "both", "case",
    "cast", "check", "collate", "column", "constraint", "current_user",
    "default", "deferrable", "distinct", "false", "foreign",
    "initially", "into", "lateral", "leading", "offset", "only",
    "placing", "primary", "references", "session_user", "some",
    "symmetric", "table", "trailing", "true", "unique", "user",
    "variadic", "window",
}
# Omitted on purpose, though reserved: they legitimately follow a `)` in
# ordinary SQL and would fire on every query — DESC/ASC after an ORDER BY
# expression, NULL after a cast, USING/FILTER/OVER after a call, DO after
# ON CONFLICT (…), RETURNING after an INSERT column list.

# `) alias` and `) AS alias` — how a subquery or a lateral gets its name.
ALIAS_RE = re.compile(r"\)\s*(?:AS\s+)?([a-zA-Z_][a-zA-Z0-9_]*)", re.IGNORECASE)


def _sql_literals(source: str) -> list[tuple[int, str]]:
    """Every triple-quoted string, with the line it starts on."""
    return [
        (source[: m.start()].count("\n") + 1, m.group(1))
        for m in re.finditer(r'"""(.*?)"""', source, re.S)
    ]


def test_no_reserved_word_subquery_aliases():
    source = QUERIES.read_text()
    offenders = []
    for line, sql in _sql_literals(source):
        if not re.search(r"\bSELECT\b", sql, re.IGNORECASE):
            continue  # a docstring, not a query
        for alias in ALIAS_RE.findall(sql):
            if alias.lower() in RESERVED_ALIASES:
                offenders.append(f"queries.py:{line}: alias `{alias}`")
    assert not offenders, (
        "reserved word used as a SQL alias — Postgres will reject the whole "
        "statement at parse time:\n  " + "\n  ".join(offenders)
    )


def test_detector_still_fires():
    """The check above passes trivially if the regex ever stops matching.
    Pin it against the exact statement that broke production."""
    bad = """
      SELECT occurred_at FROM (
        SELECT i.occurred_at FROM canonical.interaction i
        UNION ALL
        SELECT g.occurred_at FROM memory.group_mention g
      ) both ORDER BY occurred_at LIMIT 1
    """
    hits = [a for a in ALIAS_RE.findall(bad) if a.lower() in RESERVED_ALIASES]
    assert "both" in hits
