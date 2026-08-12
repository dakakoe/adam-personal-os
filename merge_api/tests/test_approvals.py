"""Tests for the approval-gating decision (sharing PR3). member_owns_all_legs
decides whether a member may edit/delete a transaction directly (they OWN every
leg account, shared or private) or whether it must go to an owner for approval.
A wrong True here would let a member silently change accounts they don't own — so
the cases are pinned."""

from __future__ import annotations

import json
from datetime import date

from merge_api.queries import member_owns_all_legs, _coerce_txn_payload

ME = "member-1"
OTHER = "member-2"


def _acc(visibility, owner):
    return {"visibility": visibility, "owner_member_id": owner}


def test_member_owns_all_legs_is_direct_even_when_shared():
    # the key fix: an owner edits their own account directly, shared OR private
    assert member_owns_all_legs(ME, ["a", "b"], [_acc("shared", ME), _acc("private", ME)]) is True


def test_single_owned_leg_is_direct():
    assert member_owns_all_legs(ME, ["a"], [_acc("shared", ME)]) is True


def test_any_leg_not_owned_needs_approval():
    assert member_owns_all_legs(ME, ["a", "b"], [_acc("shared", ME), _acc("shared", OTHER)]) is False


def test_unowned_joint_account_needs_approval():
    # shared account with no owner (NULL) → member doesn't own it → approval
    assert member_owns_all_legs(ME, ["a"], [_acc("shared", None)]) is False


def test_leg_owned_by_other_needs_approval():
    assert member_owns_all_legs(ME, ["a"], [_acc("private", OTHER)]) is False


def test_missing_account_row_needs_approval():
    # two legs but only one account found → must not be treated as owned
    assert member_owns_all_legs(ME, ["a", "b"], [_acc("shared", ME)]) is False


def test_no_member_id_needs_approval():
    assert member_owns_all_legs(None, ["a"], [_acc("private", None)]) is False


def test_no_legs_needs_approval():
    assert member_owns_all_legs(ME, [], []) is False


# --- payload JSON round-trip (regression: editing a shared txn 500'd because a
#     date in the patch isn't JSON-serializable) -------------------------------

def test_approval_payload_with_date_serializes():
    # mirrors create_approval's json.dumps(..., default=str)
    payload = {"txn_date": date(2026, 6, 16), "outflow_amount": 709.8, "note": "x"}
    s = json.dumps(payload, default=str)         # must not raise
    assert json.loads(s)["txn_date"] == "2026-06-16"


def test_coerce_txn_payload_roundtrip():
    # what comes back from JSONB → coerced back to apply-able types
    stored = {"txn_date": "2026-06-16", "outflow_amount": "709.8", "note": "x",
              "category_key": "groceries"}
    out = _coerce_txn_payload(stored)
    assert out["txn_date"] == date(2026, 6, 16)
    assert out["outflow_amount"] == 709.8
    assert out["note"] == "x" and out["category_key"] == "groceries"


def test_coerce_txn_payload_tolerates_missing_and_bad():
    assert _coerce_txn_payload({}) == {}
    assert _coerce_txn_payload({"txn_date": "not-a-date"})["txn_date"] == "not-a-date"
