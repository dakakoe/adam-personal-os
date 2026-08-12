"""chain_locked_side picks the on-chain (crypto-wallet) leg to freeze on an
imported transfer — the guard behind 'lock the chain facts, edit only the
classification'. Pure function, no DB."""

from __future__ import annotations

from merge_api.queries import chain_locked_side

WALLET, DEBT = "acc-tron", "acc-iowe"
KINDS = {WALLET: "crypto_wallet", DEBT: "debt"}


def test_non_chain_row_locks_nothing():
    assert chain_locked_side("manual", [("outflow", WALLET)], KINDS) is None
    assert chain_locked_side(None, [("inflow", WALLET)], KINDS) is None


def test_outgoing_send_locks_the_wallet_outflow():
    # a fresh single-leg send: only the wallet outflow exists
    assert chain_locked_side("chain_tx", [("outflow", WALLET)], KINDS) == "outflow"


def test_received_locks_the_wallet_inflow():
    assert chain_locked_side("chain_tx", [("inflow", WALLET)], KINDS) == "inflow"


def test_after_reconcile_still_locks_wallet_leg_not_the_debt():
    # user set the inflow to a debt account to settle; the wallet outflow stays locked
    legs = [("outflow", WALLET), ("inflow", DEBT)]
    assert chain_locked_side("chain_tx", legs, KINDS) == "outflow"


def test_no_legs_locks_nothing():
    assert chain_locked_side("chain_tx", [], KINDS) is None
