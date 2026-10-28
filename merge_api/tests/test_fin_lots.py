"""Tests for the FIFO cost-lot engine — the realized/unrealized P&L math behind
the Budget investments view. Money math is exact (Decimal), so assertions use
exact Decimal equality."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from merge_api.fin_lots import Lot, Sale, fifo_position, remaining_quantity

D = Decimal


def _lot(id, d, qty, cost):
    return Lot(id=id, open_date=date.fromisoformat(d), quantity=D(qty), cost_per_unit_usd=D(cost))


def _sale(id, d, qty, px):
    return Sale(id=id, sale_date=date.fromisoformat(d), quantity=D(qty), proceeds_per_unit_usd=D(px))


def test_single_buy_no_sells():
    p = fifo_position([_lot("a", "2026-06-01", "1", "20")], [])
    assert p.remaining_quantity == D("1")
    assert p.open_cost_usd == D("20")
    assert p.realized_gain_usd == D("0")
    assert p.avg_cost_per_unit_usd == D("20")


def test_canonical_fifo_example():
    # Buy 1@20, buy 1@30, sell 1@40 → consumes the $20 lot → realized +20,
    # one $30 lot left open.
    lots = [_lot("a", "2026-06-01", "1", "20"), _lot("b", "2026-06-02", "1", "30")]
    sales = [_sale("s1", "2026-06-10", "1", "40")]
    p = fifo_position(lots, sales)
    assert p.realized_gain_usd == D("20")
    assert p.remaining_quantity == D("1")
    assert p.open_cost_usd == D("30")          # the $30 lot remains
    assert p.sales[0].matched_quantity == D("1")
    assert p.sales[0].cost_basis_usd == D("20")
    assert p.sales[0].oversold_quantity == D("0")


def test_partial_lot_consumption():
    p = fifo_position([_lot("a", "2026-06-01", "10", "5")], [_sale("s", "2026-06-05", "3", "8")])
    assert p.realized_gain_usd == D("9")       # 3*8 - 3*5
    assert p.remaining_quantity == D("7")
    assert p.open_cost_usd == D("35")          # 7 * 5


def test_sale_spans_multiple_lots():
    # Buy 2@10, 2@20; sell 3@30 → consume 2@10 + 1@20, cost 40, proceeds 90, +50.
    lots = [_lot("a", "2026-06-01", "2", "10"), _lot("b", "2026-06-02", "2", "20")]
    p = fifo_position(lots, [_sale("s", "2026-06-09", "3", "30")])
    assert p.sales[0].cost_basis_usd == D("40")
    assert p.realized_gain_usd == D("50")
    assert p.remaining_quantity == D("1")
    assert p.open_cost_usd == D("20")          # one $20 unit left


def test_fifo_orders_by_date_not_input_order():
    # Lots passed newest-first; FIFO must still consume the OLDEST ($20) first.
    lots = [_lot("b", "2026-06-02", "1", "30"), _lot("a", "2026-06-01", "1", "20")]
    p = fifo_position(lots, [_sale("s", "2026-06-10", "1", "40")])
    assert p.sales[0].cost_basis_usd == D("20")
    assert p.open_cost_usd == D("30")


def test_same_date_lots_tiebreak_by_id_is_stable():
    lots = [_lot("zzz", "2026-06-01", "1", "30"), _lot("aaa", "2026-06-01", "1", "20")]
    p = fifo_position(lots, [_sale("s", "2026-06-10", "1", "99")])
    # "aaa" sorts first → its $20 basis is consumed.
    assert p.sales[0].cost_basis_usd == D("20")


def test_realized_loss():
    p = fifo_position([_lot("a", "2026-06-01", "1", "100")], [_sale("s", "2026-06-05", "1", "60")])
    assert p.realized_gain_usd == D("-40")
    assert p.remaining_quantity == D("0")
    assert p.open_cost_usd == D("0")
    assert p.avg_cost_per_unit_usd is None


def test_multiple_sells_net_to_zero():
    lots = [_lot("a", "2026-06-01", "3", "10")]
    sales = [_sale("s1", "2026-06-02", "1", "15"), _sale("s2", "2026-06-03", "1", "5")]
    p = fifo_position(lots, sales)
    assert p.realized_gain_usd == D("0")       # +5 then -5
    assert p.remaining_quantity == D("1")
    assert p.open_cost_usd == D("10")


def test_oversell_is_reported_not_crashed():
    # Sell 2 when only 1 was ever held: 1 matched, 1 oversold.
    p = fifo_position([_lot("a", "2026-06-01", "1", "10")], [_sale("s", "2026-06-05", "2", "20")])
    sr = p.sales[0]
    assert sr.matched_quantity == D("1")
    assert sr.oversold_quantity == D("1")
    assert sr.cost_basis_usd == D("10")
    assert sr.realized_gain_usd == D("10")     # 1 matched unit: 20 - 10
    assert p.remaining_quantity == D("0")


def test_fractional_crypto_quantities_exact():
    # 0.5 SOL @ $20, 0.25 @ $40; sell 0.6 @ $50.
    lots = [_lot("a", "2026-06-01", "0.5", "20"), _lot("b", "2026-06-02", "0.25", "40")]
    p = fifo_position(lots, [_sale("s", "2026-06-10", "0.6", "50")])
    # consumes 0.5@20 (=10) + 0.1@40 (=4) → basis 14; proceeds 0.6*50 = 30 → +16
    assert p.sales[0].cost_basis_usd == D("14.0")
    assert p.realized_gain_usd == D("16.00")
    assert p.remaining_quantity == D("0.15")   # 0.25 - 0.10
    assert p.open_cost_usd == D("6.00")        # 0.15 * 40


def test_unrealized_gain_marks_open_lots_to_market():
    p = fifo_position(
        [_lot("a", "2026-06-01", "1", "20"), _lot("b", "2026-06-02", "1", "30")],
        [_sale("s", "2026-06-10", "1", "40")],  # closes the $20 lot
    )
    # one $30 lot open; at price 50 → unrealized = 1*50 - 30 = 20
    assert p.unrealized_gain_usd(D("50")) == D("20")


def test_empty_position():
    p = fifo_position([], [])
    assert p.remaining_quantity == D("0")
    assert p.realized_gain_usd == D("0")
    assert p.avg_cost_per_unit_usd is None


def test_remaining_quantity_helper():
    assert remaining_quantity([_lot("a", "2026-06-01", "5", "10")],
                              [_sale("s", "2026-06-02", "2", "12")]) == D("3")
