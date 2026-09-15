"""findpeople.py — the pure shaping around find_people (no DB, no model)."""
from __future__ import annotations

from datetime import datetime

from pathlib import Path

from server.findpeople import (RANK_SQL, describe, normalize_circles, relationship,
                               shape, snippet, unmatched)

SERVER = Path(__file__).resolve().parents[1] / "server"


def _row(**kw):
    base = {"person_id": "p1", "display_name": "Dana Lee", "sensitive": False,
            "in_requested": True, "card_text": "Dana Lee. Partner at a fund.",
            "circles": ["Investors", "Friends"], "known": True,
            "last_contact": datetime(2026, 8, 1, 12, 0), "distance": 0.123456}
    return {**base, **kw}


def _ev(body="Happy to look at the deck", d=0.2):
    return {"occurred_at": datetime(2026, 7, 3), "channel": "telegram",
            "direction": "inbound", "body": body, "distance": d}


def test_normalize_circles_lowercases_dedupes_and_keeps_order():
    assert normalize_circles(["Investors", " exchanges ", "investors", ""]) == ["investors", "exchanges"]
    assert normalize_circles("Lawyers, Payments") == ["lawyers", "payments"]
    assert normalize_circles(None) == [] and normalize_circles([]) == []


def test_unmatched_accepts_key_or_label():
    matched = [{"key": "miners-datacenters", "label": "Miners/Datacenters"}]
    assert unmatched(["miners/datacenters", "miners-datacenters", "rwa"], matched) == ["rwa"]


def test_relationship_tiers():
    assert relationship(True, None) == "known"
    assert relationship(False, datetime(2026, 1, 1)) == "wrote to you, never answered"
    assert relationship(False, None) == "never talked"


def test_snippet_collapses_whitespace_and_caps():
    assert snippet("a\n\n  b") == "a b"
    long = snippet("x" * 500, n=10)
    assert len(long) == 10 and long.endswith("…")
    assert snippet(None) == ""


def test_shape_full_result():
    out = shape(_row(), [_ev()])
    assert out["card"] == "Dana Lee. Partner at a fund."
    assert out["circles"] == ["Investors", "Friends"]
    assert out["relationship"] == "known" and out["last_contact"] == "2026-08-01"
    assert out["distance"] == 0.1235
    assert out["evidence"] == [{"date": "2026-07-03", "channel": "telegram",
                                "direction": "inbound", "snippet": "Happy to look at the deck",
                                "distance": 0.2}]


def test_shape_sensitive_is_name_only():
    out = shape(_row(sensitive=True), [_ev("private detail")])
    assert set(out) == {"person_id", "name", "in_requested_circle", "note"}
    assert "private detail" not in str(out)


def test_shape_circle_member_without_card_or_contact():
    out = shape(_row(card_text=None, distance=None, known=False, last_contact=None,
                     circles=None), [])
    assert out["distance"] is None and out["card"] is None
    assert out["circles"] == [] and out["relationship"] == "never talked"


def test_describe_lists_circles_and_warns_some_people_never_come_back():
    text = describe([{"label": "Investors", "members": 71}, {"label": "Lawyers", "members": 7}])
    assert "Investors (71), Lawyers (7)" in text
    assert "never returned" in text


def test_describe_without_catalog_still_explains_the_tool():
    assert "circles" in describe([]) and "Circles (members)" not in describe([])


def test_exclusion_is_enforced_in_the_ranking_sql_itself():
    # Not a prompt, not a post-filter: the barred CTE gates every candidate.
    assert "NOT EXISTS (SELECT 1 FROM barred" in RANK_SQL


def test_the_exclusion_is_configuration_not_code():
    """Which circle means "never suggest these people" is the user's business.
    It sat in recall.py as a literal until 2026-09-14, where publishing the
    source would have published it."""
    for name in ("findpeople.py", "recall.py", "tools.py"):
        assert "EXCLUDED_CIRCLES = " not in (SERVER / name).read_text()
    assert "FIND_PEOPLE_EXCLUDED_CIRCLES" in (SERVER / "config.py").read_text()


def test_excluded_circles_env_is_parsed_as_a_key_list(monkeypatch):
    from server import config
    monkeypatch.setenv("FIND_PEOPLE_EXCLUDED_CIRCLES", " Do-Not-Contact , blocked ,")
    assert config._csv_env("FIND_PEOPLE_EXCLUDED_CIRCLES") == ("do-not-contact", "blocked")
    monkeypatch.delenv("FIND_PEOPLE_EXCLUDED_CIRCLES")
    assert config._csv_env("FIND_PEOPLE_EXCLUDED_CIRCLES") == ()
