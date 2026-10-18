"""Pure retrieval helpers for local_ask: merge interaction + mail hits by
vector distance and format the excerpts/sources the prompt is built from.
Kept free of DB/HTTP so they're testable with bare pytest."""
from __future__ import annotations

from typing import Any, Mapping, Sequence


def merge_by_distance(
    interaction_rows: Sequence[Mapping[str, Any]],
    mail_rows: Sequence[Mapping[str, Any]],
    k: int,
) -> list[tuple[str, Mapping[str, Any]]]:
    """Merge two distance-sorted result sets into one ascending-distance list
    of (kind, row), capped at k. Rows missing a distance sort last; ties keep
    interactions first (stable order)."""
    tagged = [("interaction", r) for r in interaction_rows] + [("mail", r) for r in mail_rows]
    tagged.sort(key=lambda t: (t[1].get("distance") is None,
                               float(t[1].get("distance") or 0.0)))
    return tagged[: max(0, k)]


def format_interaction_excerpt(row: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """[DATE] NAME (CHANNEL, DIRECTION): BODY[:400] — the shape local_ask has
    always used. Returns (excerpt, source)."""
    who = row.get("display_name") or "(unknown)"
    occ = row.get("occurred_at")
    when = occ.date().isoformat() if occ is not None and hasattr(occ, "date") else "?"
    body = (row.get("body") or "").strip().replace("\n", " ")[:400]
    excerpt = f"[{when}] {who} ({row.get('channel')}, {row.get('direction')}): {body}"
    source = {"kind": "interaction", "occurred_at": when, "display_name": who,
              "channel": row.get("channel"), "snippet": body[:160]}
    return excerpt, source


def format_mail_excerpt(row: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """[DATE] NAME <addr> (mail): SUBJECT — BODY[:400]. Returns (excerpt, source)."""
    name = (row.get("from_name") or "").strip()
    addr = (row.get("from_address") or "").strip() or "(unknown)"
    who = f"{name} <{addr}>" if name else addr
    occ = row.get("internal_date")
    when = occ.date().isoformat() if occ is not None and hasattr(occ, "date") else "?"
    subj = (row.get("subject") or "(no subject)").strip()
    body = (row.get("body_text") or "").strip().replace("\n", " ")[:400]
    excerpt = f"[{when}] {who} (mail): {subj} — {body}"
    source = {"kind": "mail", "occurred_at": when, "from": who,
              "subject": subj[:160], "snippet": body[:160]}
    return excerpt, source
