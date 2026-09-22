"""_reconcile_directions must recover debit/credit from the running-balance
movement — the fix for single-signed-column statements (Sber: '+25 000,00' in,
'25 000,00' out) where the two-column heuristic mislabels direction. Pure
function, no network."""

from __future__ import annotations

from merge_api.finance_import import _reconcile_directions


# Real chain from the Sber statement (newest-first, each balance is post-txn):
#   +3 730 → 6504.10 · 25 000 out → 2774.10 · +25 000 → 27774.10 ·
#   10 248 out → 2774.10 · 10 000 out → 13022.10 · +17 500 → 23022.10
_CHAIN = [
    {"amount": 3730.0, "balance": 6504.10},
    {"amount": 25000.0, "balance": 2774.10},
    {"amount": 25000.0, "balance": 27774.10},
    {"amount": 10248.0, "balance": 2774.10},
    {"amount": 10000.0, "balance": 13022.10},
    {"amount": 17500.0, "balance": 23022.10},
]
_EXPECTED = ["credit", "debit", "credit", "debit", "debit", "credit"]


def _rows(direction: str) -> list[dict]:
    return [{**r, "direction": direction} for r in _CHAIN]


def test_corrects_all_wrong_directions():
    out = _reconcile_directions(_rows("credit"))  # model got every one wrong
    assert [r["direction"] for r in out] == _EXPECTED


def test_stable_when_already_correct():
    rows = [{**r, "direction": d} for r, d in zip(_CHAIN, _EXPECTED)]
    assert [r["direction"] for r in _reconcile_directions(rows)] == _EXPECTED


def test_no_running_balance_leaves_model_directions():
    # No balances at all → nothing provable → keep the model's call untouched.
    rows = [{"amount": r["amount"], "direction": "debit"} for r in _CHAIN]
    assert [r["direction"] for r in _reconcile_directions(rows)] == ["debit"] * len(_CHAIN)


def test_row_without_balance_kept_others_fixed():
    rows = _rows("credit")  # all wrong to start
    rows[0] = {"amount": 3730.0, "direction": "credit"}  # drop the newest row's balance
    out = _reconcile_directions(rows)
    # the balance-less row can't be proven → keeps the model's call; every other
    # row still reconciles from its own adjacent balance step
    assert out[0]["direction"] == "credit"
    assert [r["direction"] for r in out[1:5]] == ["debit", "credit", "debit", "debit"]
