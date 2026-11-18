"""Tests for the sharing PR1 access-control predicate — the single source of
truth that scopes every account-bearing finance read to what a member may see.
A bug here is a money-data leak, so the cases below pin the exact SQL + binds."""

from __future__ import annotations

from merge_api.queries import account_visibility_clause


def test_owner_viewer_none_no_filter():
    """None viewer = app-owner (admin/bot/workers) → no restriction, no binds."""
    params: list = []
    assert account_visibility_clause("a", None, params) == ""
    assert params == []


def test_is_owner_flag_no_filter():
    params: list = []
    assert account_visibility_clause("a", {"is_owner": True, "member_id": "x"}, params) == ""
    assert params == []


def test_role_owner_no_filter():
    params: list = []
    assert account_visibility_clause("a", {"role": "owner", "member_id": "x"}, params) == ""
    assert params == []


def test_member_gets_shared_or_owned_clause():
    params: list = []
    clause = account_visibility_clause("a", {"role": "member", "member_id": "m1", "is_owner": False}, params)
    assert clause == " AND (a.visibility = 'shared' OR a.owner_member_id = $1::uuid)"
    assert params == ["m1"]


def test_member_clause_uses_correct_positional_with_existing_params():
    params: list = ["2026-01-01", "2026-12-31"]   # two pre-existing binds
    clause = account_visibility_clause("t", {"role": "member", "member_id": "m9"}, params)
    assert clause == " AND (t.visibility = 'shared' OR t.owner_member_id = $3::uuid)"
    assert params == ["2026-01-01", "2026-12-31", "m9"]


def test_member_without_member_id_still_filters_shared_only():
    """A budget caller with no fin_member row yet must NOT see private accounts:
    the clause is still emitted; the None bind makes owner_member_id = NULL never
    match, so only shared rows pass."""
    params: list = []
    clause = account_visibility_clause("a", {"role": "member", "member_id": None}, params)
    assert clause == " AND (a.visibility = 'shared' OR a.owner_member_id = $1::uuid)"
    assert params == [None]


def test_alias_is_respected():
    params: list = []
    clause = account_visibility_clause("acc", {"role": "member", "member_id": "m1"}, params)
    assert "acc.visibility" in clause and "acc.owner_member_id" in clause
