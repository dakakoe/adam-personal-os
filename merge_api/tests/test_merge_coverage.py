"""Guards that execute_merge redirects EVERY column referencing
canonical.person. A missed one leaves a dangling reference that renders as a
blank person link (opportunity counterparties, task/suggestion people, finance
counterparties, …). This parses the migrations for person FKs and asserts each
is classified in queries.py, so a newly-added person FK fails here until it is
handled — the failure mode that shipped the original bug.

Assumes each person FK is declared on a single line as
`<col> ... REFERENCES canonical.person` (inline in CREATE TABLE or via
ADD COLUMN), which is the style used throughout migrations/."""

from __future__ import annotations

import re
from pathlib import Path

from merge_api import queries

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"

_CREATE = re.compile(r"^\s*CREATE TABLE (?:IF NOT EXISTS )?([a-z_]+\.[a-z_]+)", re.I)
_ALTER = re.compile(r"^\s*ALTER TABLE (?:ONLY )?([a-z_]+\.[a-z_]+)", re.I)
_ADD_COLUMN = re.compile(r"\bADD COLUMN\s+([a-z_]+)", re.I)
_INLINE_COLUMN = re.compile(r"^\s*([a-z_]+)\b")  # lowercase: skips SQL keywords


def _declared_person_fks() -> set[tuple[str, str]]:
    """Every (schema.table, column) that REFERENCES canonical.person, per the
    migration SQL."""
    found: set[tuple[str, str]] = set()
    for path in sorted(MIGRATIONS.glob("*.sql")):
        table: str | None = None
        for line in path.read_text().splitlines():
            m = _CREATE.match(line) or _ALTER.match(line)
            if m:
                table = m.group(1)
            if "REFERENCES canonical.person" not in line:
                continue
            col = _ADD_COLUMN.search(line) or _INLINE_COLUMN.match(line)
            if table and col:
                found.add((table, col.group(1)))
    return found


def test_migrations_dir_found():
    assert MIGRATIONS.is_dir(), f"migrations dir not at {MIGRATIONS}"


def test_execute_merge_covers_every_person_fk():
    declared = _declared_person_fks()
    assert declared, "parser found no person FKs — the regex or path drifted"

    covered = (
        {(t, c) for t, c, _ in queries._MERGE_REPOINTS}
        | set(queries._MERGE_DROP)
        | queries._MERGE_SKIP
    )
    missing = declared - covered
    assert not missing, (
        "person FK(s) not handled by execute_merge: "
        f"{sorted(missing)}. Add each to _MERGE_REPOINTS (repoint), or to "
        "_MERGE_DROP / _MERGE_SKIP with a reason."
    )


def test_merge_lists_have_no_duplicates_or_overlap():
    repoint = [(t, c) for t, c, _ in queries._MERGE_REPOINTS]
    assert len(repoint) == len(set(repoint)), "duplicate entry in _MERGE_REPOINTS"
    assert not (set(repoint) & set(queries._MERGE_DROP)), "repoint/drop overlap"
    assert not (set(repoint) & queries._MERGE_SKIP), "repoint/skip overlap"


def test_repoint_sql_shapes():
    assert queries._merge_repoint_sql("memory.opportunity", "counterparty_id") == (
        "UPDATE memory.opportunity SET counterparty_id = $1::uuid "
        "WHERE counterparty_id = $2::uuid"
    )
    # person-unique table: dedup deletes the loser row only if a winner row exists
    solo = queries._merge_dedup_sql("memory.person_photo", "person_id", ())
    assert "USING memory.person_photo AS w" in solo
    assert "l.person_id = $2::uuid" in solo and "w.person_id = $1::uuid" in solo
    assert "AND l." not in solo  # no extra match columns
    # composite-unique table: dedup also matches the other key column
    pair = queries._merge_dedup_sql("memory.company_person", "person_id", ("company_id",))
    assert "AND l.company_id = w.company_id" in pair
