"""FIFO cost-lot accounting for the Budget investments view.

The model is intentionally STATELESS: buys (lots) and sells are immutable facts;
remaining quantity, realized gain, and cost basis are *derived* by replaying
sells against lots first-in-first-out. There is no mutable `remaining_quantity`
column to drift out of sync — editing or deleting a buy/sell just changes the
inputs and everything recomputes. All money/quantity math is Decimal (never
float — this is finance).

The API layer adds the only thing this module can't know: the *current* price,
to turn open lots into unrealized P&L.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

ZERO = Decimal(0)


@dataclass(frozen=True)
class Lot:
    """A buy. Immutable fact."""
    id: str
    open_date: date
    quantity: Decimal
    cost_per_unit_usd: Decimal


@dataclass(frozen=True)
class Sale:
    """A sell. Immutable fact."""
    id: str
    sale_date: date
    quantity: Decimal
    proceeds_per_unit_usd: Decimal


@dataclass
class LotState:
    """A lot plus how much of it is still open after FIFO matching."""
    lot: Lot
    remaining_quantity: Decimal

    @property
    def open_cost_usd(self) -> Decimal:
        return self.remaining_quantity * self.lot.cost_per_unit_usd


@dataclass
class SaleResult:
    """Realized outcome of one sell after FIFO-consuming lots."""
    sale: Sale
    matched_quantity: Decimal       # qty actually covered by lots
    cost_basis_usd: Decimal         # cost of the consumed lots
    proceeds_usd: Decimal           # qty * proceeds_per_unit
    realized_gain_usd: Decimal      # proceeds - cost_basis (for matched qty)
    oversold_quantity: Decimal      # qty sold beyond available lots (data error if > 0)


@dataclass
class Position:
    """Derived state of one (account, asset) position."""
    remaining_quantity: Decimal = ZERO
    open_cost_usd: Decimal = ZERO            # cost basis of the still-open lots
    realized_gain_usd: Decimal = ZERO        # summed across all sells
    lots: list[LotState] = field(default_factory=list)
    sales: list[SaleResult] = field(default_factory=list)

    @property
    def avg_cost_per_unit_usd(self) -> Decimal | None:
        if self.remaining_quantity <= ZERO:
            return None
        return self.open_cost_usd / self.remaining_quantity

    def unrealized_gain_usd(self, current_price_usd: Decimal) -> Decimal:
        """Mark-to-market the open lots at a current price."""
        return self.remaining_quantity * current_price_usd - self.open_cost_usd


def fifo_position(lots: list[Lot], sales: list[Sale]) -> Position:
    """Replay `sales` against `lots` FIFO for ONE (account, asset) and return the
    derived position. Lots consume oldest-first (by open_date, then id for a
    stable tiebreak); sales apply oldest-first.

    Oversell (selling more than was ever held) is reported per-sale via
    `oversold_quantity` and excluded from cost basis — the caller validates and
    rejects oversells before persisting; the engine stays total so a bad row
    can't crash a whole portfolio read."""
    states = [
        LotState(lot, lot.quantity)
        for lot in sorted(lots, key=lambda l: (l.open_date, l.id))
    ]
    sales_sorted = sorted(sales, key=lambda s: (s.sale_date, s.id))

    results: list[SaleResult] = []
    total_realized = ZERO
    for sale in sales_sorted:
        to_match = sale.quantity
        cost_basis = ZERO
        for st in states:
            if to_match <= ZERO:
                break
            if st.remaining_quantity <= ZERO:
                continue
            take = min(st.remaining_quantity, to_match)
            cost_basis += take * st.lot.cost_per_unit_usd
            st.remaining_quantity -= take
            to_match -= take
        matched = sale.quantity - to_match
        proceeds = sale.quantity * sale.proceeds_per_unit_usd
        # Realized gain is only meaningful for the matched portion; proceeds for
        # the matched units = matched * price.
        matched_proceeds = matched * sale.proceeds_per_unit_usd
        realized = matched_proceeds - cost_basis
        total_realized += realized
        results.append(SaleResult(
            sale=sale, matched_quantity=matched, cost_basis_usd=cost_basis,
            proceeds_usd=proceeds, realized_gain_usd=realized,
            oversold_quantity=to_match,
        ))

    remaining = sum((st.remaining_quantity for st in states), ZERO)
    open_cost = sum((st.open_cost_usd for st in states), ZERO)
    return Position(
        remaining_quantity=remaining, open_cost_usd=open_cost,
        realized_gain_usd=total_realized, lots=states, sales=results,
    )


def remaining_quantity(lots: list[Lot], sales: list[Sale]) -> Decimal:
    """Convenience: open quantity only (for sell-validation before persisting)."""
    return fifo_position(lots, sales).remaining_quantity
