"""Budget tools: the two-leg body builder, the shaping, and the guards."""
from __future__ import annotations

import asyncio
from datetime import date

import httpx
import pytest

from server import assistant, finance

A1 = "11111111-1111-1111-1111-111111111111"
A2 = "22222222-2222-2222-2222-222222222222"
SENS = "99999999-9999-9999-9999-999999999999"
TODAY = date(2026, 10, 28)


def test_month_range_defaults_to_current_month_and_parses_yyyy_mm() -> None:
    assert finance.month_range(None, TODAY) == (date(2026, 10, 1), date(2026, 10, 31))
    assert finance.month_range("2026-02", TODAY) == (date(2026, 2, 1), date(2026, 2, 28))
    assert finance.month_range("2026-02-15", TODAY)[0] == date(2026, 2, 1)
    with pytest.raises(assistant.ToolError):
        finance.month_range("October", TODAY)


def test_txn_body_puts_the_amount_on_the_right_leg() -> None:
    exp = finance.txn_body("expense", 350, A1, txn_date=TODAY, category_key="food", payee="7-Eleven")
    assert exp == {"txn_date": "2026-10-28", "category_key": "food", "payee_text": "7-Eleven",
                   "tags": [], "outflow_account_id": A1, "outflow_amount": 350}
    inc = finance.txn_body("income", 1000, A1, txn_date=TODAY)
    assert inc["inflow_account_id"] == A1 and inc["inflow_amount"] == 1000 and "outflow_amount" not in inc
    tr = finance.txn_body("transfer", 100, A1, txn_date=TODAY, to_account_id=A2, to_amount=3500)
    assert (tr["outflow_account_id"], tr["outflow_amount"], tr["inflow_account_id"], tr["inflow_amount"]) \
        == (A1, 100, A2, 3500)
    same = finance.txn_body("transfer", 100, A1, txn_date=TODAY, to_account_id=A2)
    assert same["inflow_amount"] == 100                       # same currency: mirror the amount


def test_txn_body_rejects_bad_kind_amount_and_transfer_shapes() -> None:
    with pytest.raises(assistant.ToolError):
        finance.txn_body("refund", 10, A1, txn_date=TODAY)
    with pytest.raises(assistant.ToolError):
        finance.txn_body("expense", 0, A1, txn_date=TODAY)
    with pytest.raises(assistant.ToolError):
        finance.txn_body("expense", 10, "not-an-id", txn_date=TODAY)
    with pytest.raises(assistant.ToolError):
        finance.txn_body("transfer", 10, A1, txn_date=TODAY)                 # no receiving account
    with pytest.raises(assistant.ToolError):
        finance.txn_body("transfer", 10, A1, txn_date=TODAY, to_account_id=A1)


def test_amount_patch_targets_the_existing_leg_and_refuses_transfers() -> None:
    assert finance.amount_patch({"txn_type": "expense", "outflow_account_id": A1}, 42) == {"outflow_amount": 42}
    assert finance.amount_patch({"txn_type": "income", "inflow_account_id": A1}, 42) == {"inflow_amount": 42}
    with pytest.raises(assistant.ToolError):
        finance.amount_patch({"txn_type": "transfer", "outflow_account_id": A1}, 42)


def test_compact_txn_reads_the_active_leg_and_hides_sensitive_people() -> None:
    row = {"id": "t1", "txn_date": "2026-10-27", "txn_type": "expense", "outflow_amount": 1200.0,
           "outflow_asset_code": "THB", "outflow_account_name": "Kasikorn", "category_key": "food",
           "payee_text": "Lunch", "person_id": SENS, "person_name": "Secret Person", "tags": []}
    out = finance.compact_txn(row, frozenset({SENS}))
    assert out["amount"] == 1200.0 and out["currency"] == "THB" and out["account"] == "Kasikorn"
    assert "Secret" not in str(out) and "person_id" not in out
    plain = finance.compact_txn({**row, "person_id": A1, "person_name": "Open Person"}, frozenset({SENS}))
    assert plain["person"] == "Open Person" and plain["person_id"] == A1
    tr = finance.compact_txn({"id": "t2", "txn_type": "transfer", "outflow_amount": 100, "outflow_asset_code": "USD",
                              "outflow_account_name": "Wise", "inflow_amount": 3500, "inflow_asset_code": "THB",
                              "inflow_account_name": "Kasikorn"})
    assert tr["to_account"] == "Kasikorn" and tr["to_amount"] == 3500 and tr["to_currency"] == "THB"


def test_budget_status_view_sorts_by_usage_and_names_overruns() -> None:
    rows = [{"category_key": "food", "category_label": "Food", "limit_usd": 500, "actual_usd": 620},
            {"category_key": "fun", "category_label": "Fun", "limit_usd": 200, "actual_usd": 50},
            {"category_key": "misc", "category_label": "Misc", "limit_usd": 0, "actual_usd": 10}]
    v = finance.budget_status_view(rows, date(2026, 10, 1), date(2026, 10, 31))
    assert v["month"] == "2026-10" and v["limit_usd"] == 700 and v["spent_usd"] == 680
    assert v["over_budget"] == ["food"]
    assert [b["category"] for b in v["budgets"]] == ["food", "fun", "misc"]
    assert v["budgets"][0]["left_usd"] == -120 and v["budgets"][0]["used_pct"] == 124
    assert v["budgets"][2]["used_pct"] is None


def test_compact_account_and_planned_keep_balances_and_next_date() -> None:
    a = finance.compact_account({"id": A1, "name": "Kasikorn", "kind": "bank", "account_class": "operational",
                                 "owner": "me", "currency_code": "THB",
                                 "balances": [{"asset_code": "THB", "balance": 15000.5, "usd_value": 450.0}],
                                 "archived": False})
    assert a["balances"] == [{"currency": "THB", "amount": 15000.5, "usd_value": 450.0}] and "archived" not in a
    p = finance.compact_planned({"id": A2, "name": "Rent", "txn_type": "expense", "outflow_amount": 25000,
                                 "outflow_asset_code": "THB", "outflow_account_name": "Kasikorn",
                                 "freq": "monthly", "next_date": "2026-11-01", "auto_post": False, "active": True})
    assert p["amount"] == 25000 and p["next_date"] == "2026-11-01" and p["freq"] == "monthly"


def test_register_adds_the_budget_tools_and_update_fetches_before_an_amount_change(monkeypatch) -> None:
    from mcp.server.fastmcp import FastMCP
    from types import SimpleNamespace

    calls: list[tuple[str, str, bytes]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.content))
        if request.method == "GET" and request.url.path.endswith("/transactions/" + A1):
            return httpx.Response(200, json={"id": A1, "txn_type": "expense", "outflow_account_id": A2})
        return httpx.Response(200, json={"id": A1, "txn_type": "expense", "outflow_amount": 99,
                                         "outflow_asset_code": "THB", "txn_date": "2026-10-28"})

    mcp = FastMCP("t")
    cfg = SimpleNamespace(merge_api_token="tok", merge_api_base="http://api.test", timezone="UTC",
                          tg_bot_token=None, tg_chat_id=None)
    assistant.register(mcp, cfg, transport=httpx.MockTransport(handler))
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert {"budget_status", "spending_report", "cashflow", "list_transactions", "list_accounts",
            "list_categories", "net_worth", "list_planned", "pending_approvals", "add_transaction",
            "update_transaction", "set_budget", "post_planned", "decide_approval"} <= names

    async def no_sensitive() -> frozenset[str]:
        return frozenset()

    monkeypatch.setattr(finance, "sensitive_ids", no_sensitive)
    asyncio.run(mcp.call_tool("update_transaction", {"txn_id": A1, "amount": 99}))
    assert [(m, p) for m, p, _ in calls] == [("GET", f"/api/finance/transactions/{A1}"),
                                             ("PATCH", f"/api/finance/transactions/{A1}")]
    assert b'"outflow_amount": 99' in calls[1][2] or b'"outflow_amount":99' in calls[1][2]
