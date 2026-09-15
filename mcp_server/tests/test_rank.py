"""rank.py — the merge/format layer under local_ask (pure, no DB/model)."""
from __future__ import annotations

from datetime import datetime

from server.rank import format_interaction_excerpt, format_mail_excerpt, merge_by_distance


def _i(d):
    return {"distance": d, "occurred_at": datetime(2026, 7, 1), "direction": "inbound",
            "channel": "telegram", "display_name": "Alex", "body": "hi"}


def _m(d):
    return {"distance": d, "internal_date": datetime(2026, 7, 2),
            "from_address": "a@b.c", "from_name": "Al", "subject": "Sub", "body_text": "text"}


def test_merge_orders_by_distance_across_kinds() -> None:
    out = merge_by_distance([_i(0.3), _i(0.5)], [_m(0.2), _m(0.4)], k=10)
    assert [(kind, r["distance"]) for kind, r in out] == [
        ("mail", 0.2), ("interaction", 0.3), ("mail", 0.4), ("interaction", 0.5)]


def test_merge_caps_at_k() -> None:
    out = merge_by_distance([_i(0.1)] * 5, [_m(0.2)] * 5, k=3)
    assert len(out) == 3


def test_merge_ties_keep_interactions_first_and_none_last() -> None:
    out = merge_by_distance([_i(0.2), {**_i(None), "distance": None}], [_m(0.2)], k=10)
    assert [kind for kind, _ in out] == ["interaction", "mail", "interaction"]


def test_merge_empty_inputs() -> None:
    assert merge_by_distance([], [], k=5) == []


def test_interaction_excerpt_shape() -> None:
    excerpt, source = format_interaction_excerpt(_i(0.1))
    assert excerpt == "[2026-07-01] Alex (telegram, inbound): hi"
    assert source["kind"] == "interaction"
    assert source["display_name"] == "Alex"


def test_mail_excerpt_shape_and_none_fields() -> None:
    excerpt, source = format_mail_excerpt(_m(0.1))
    assert excerpt == "[2026-07-02] Al <a@b.c> (mail): Sub — text"
    assert source["kind"] == "mail"

    bare = {"distance": 0.1, "internal_date": None, "from_address": None,
            "from_name": None, "subject": None, "body_text": None}
    excerpt, source = format_mail_excerpt(bare)
    assert excerpt == "[?] (unknown) (mail): (no subject) — "
    assert source["subject"] == "(no subject)"
