"""is_uuid — the guard that turns a malformed/empty account_id into a 400 on
GET /api/finance/positions instead of a 500 from a deep `$1::uuid` cast."""

from __future__ import annotations

from merge_api.queries import is_uuid


def test_valid_uuid():
    assert is_uuid("00000000-0000-0000-0000-000000000000")
    assert is_uuid("3c53cdc0-1111-2222-3333-444455556666")


def test_empty_and_none_are_invalid():
    assert not is_uuid("")
    assert not is_uuid(None)


def test_malformed_is_invalid():
    assert not is_uuid("not-a-uuid")
    assert not is_uuid("123")
    assert not is_uuid("3c53cdc0-1111-2222-3333")  # too short
