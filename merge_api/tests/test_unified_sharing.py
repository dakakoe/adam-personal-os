"""Tests for the entity-agnostic ACL primitive (unified-sharing foundation).
The generic visibility_clause/visible_entity_ids/member_can_see must behave
identically to the finance wrappers and short-circuit (no DB) for app-owners."""

from __future__ import annotations

import asyncio

from merge_api.queries import (
    _viewer_sees_all, visibility_clause, account_visibility_clause,
    visible_entity_ids, member_can_see,
)

MEMBER = {"role": "member", "member_id": "m1", "is_owner": False}


def test_viewer_sees_all():
    assert _viewer_sees_all(None) is True
    assert _viewer_sees_all({"is_owner": True}) is True
    assert _viewer_sees_all({"role": "owner", "member_id": "x"}) is True
    assert _viewer_sees_all(MEMBER) is False


def test_visibility_clause_is_entity_agnostic():
    # same predicate shape for any alias / table
    for alias in ("a", "c", "t"):
        params: list = []
        clause = visibility_clause(alias, MEMBER, params)
        assert clause == f" AND ({alias}.visibility = 'shared' OR {alias}.owner_member_id = $1::uuid)"
        assert params == ["m1"]


def test_owner_clause_empty():
    params: list = []
    assert visibility_clause("a", None, params) == ""
    assert params == []


def test_account_wrapper_delegates_identically():
    p1: list = []
    p2: list = []
    assert account_visibility_clause("a", MEMBER, p1) == visibility_clause("a", MEMBER, p2)
    assert p1 == p2 == ["m1"]


def test_visible_entity_ids_owner_short_circuits_without_db():
    # None viewer / owner → None, and crucially no pool access (pass None)
    assert asyncio.run(visible_entity_ids(None, None)) is None
    assert asyncio.run(visible_entity_ids(None, {"role": "owner", "member_id": "x"})) is None


def test_member_can_see_owner_short_circuits_without_db():
    assert asyncio.run(member_can_see(None, None, "any-id")) is True
    assert asyncio.run(member_can_see(None, {"is_owner": True}, "any-id")) is True
