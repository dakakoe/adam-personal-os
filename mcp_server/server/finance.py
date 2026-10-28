"""Budget tools for the assistant connector: the owner's family ledger at
/budget, read and written through merge_api's /api/finance routes.

Same shape as assistant.py — thin httpx calls with the admin token, compact
rows, and the sensitive rule applied to the one place a person can appear
(a transaction's counterparty). Amounts are in the account's own currency;
reports and budgets are in USD, the way the /budget page shows them.
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date
from typing import Any, Callable

from mcp.server.fastmcp import FastMCP

from . import sensitive as sens
from .assistant import READ, WRITE, MergeAPI, ToolError, _clean, require_id, sensitive_ids

TXN_KINDS = ("expense", "income", "transfer")
MAX_TXNS = 200
MAX_ASSET_LINES = 15


# --- shaping ---------------------------------------------------------------

def month_range(month: str | None, today: date) -> tuple[date, date]:
    """'2026-10' (or None = the current month) → first day, last day."""
    if month:
        try:
            y, m = (int(p) for p in month.strip()[:7].split("-"))
            first = date(y, m, 1)
        except (TypeError, ValueError):
            raise ToolError(f"month must look like 2026-10, got {month!r}") from None
    else:
        first = today.replace(day=1)
    return first, first.replace(day=monthrange(first.year, first.month)[1])


def _amount(t: dict) -> tuple[float | None, str | None]:
    if t.get("outflow_amount") is not None:
        return t.get("outflow_amount"), t.get("outflow_asset_code")
    return t.get("inflow_amount"), t.get("inflow_asset_code")


def compact_txn(t: dict, ids: frozenset[str] = frozenset()) -> dict:
    amount, currency = _amount(t)
    out = {
        "id": t.get("id"), "date": t.get("txn_date"), "type": t.get("txn_type"),
        "amount": amount, "currency": currency,
        "account": t.get("outflow_account_name") or t.get("inflow_account_name"),
        "category": t.get("category_key"), "payee": t.get("payee_text"),
        "note": t.get("note"), "tags": t.get("tags") or [],
    }
    if t.get("txn_type") == "transfer":
        out["to_account"] = t.get("inflow_account_name")
        out["to_amount"] = t.get("inflow_amount")
        out["to_currency"] = t.get("inflow_asset_code")
    if t.get("usd_value") is not None:
        out["usd_value"] = t["usd_value"]
    if t.get("person_id"):
        if t["person_id"] in ids:
            out["person"] = sens.NOTE
        else:
            out["person"] = t.get("person_name")
            out["person_id"] = t["person_id"]
    return {k: v for k, v in out.items() if v not in (None, [])}


def compact_budget(b: dict) -> dict:
    limit = float(b.get("limit_usd") or 0)
    actual = float(b.get("actual_usd") or 0)
    return {"category": b.get("category_key"), "label": b.get("category_label"),
            "limit_usd": round(limit, 2), "spent_usd": round(actual, 2),
            "left_usd": round(limit - actual, 2),
            "used_pct": round(100 * actual / limit) if limit else None}


def budget_status_view(rows: list[dict], first: date, last: date) -> dict:
    items = sorted((compact_budget(b) for b in rows),
                   key=lambda x: -(x["used_pct"] or 0))
    over = [x["category"] for x in items if x["used_pct"] is not None and x["used_pct"] >= 100]
    return {"month": first.strftime("%Y-%m"), "from": first.isoformat(), "to": last.isoformat(),
            "limit_usd": round(sum(x["limit_usd"] for x in items), 2),
            "spent_usd": round(sum(x["spent_usd"] for x in items), 2),
            "over_budget": over, "budgets": items}


def compact_account(a: dict) -> dict:
    return {k: v for k, v in {
        "id": a.get("id"), "name": a.get("name"), "kind": a.get("kind"),
        "class": a.get("account_class"), "owner": a.get("owner"),
        "currency": a.get("currency_code"), "institution": a.get("institution"),
        "balances": [{k: v for k, v in {"currency": b.get("asset_code"), "amount": b.get("balance"),
                                        "usd_value": b.get("usd_value")}.items() if v is not None}
                     for b in (a.get("balances") or [])],
        "archived": a.get("archived") or None,
    }.items() if v not in (None, [])}


def compact_planned(p: dict) -> dict:
    amount, currency = _amount(p)
    return {k: v for k, v in {
        "id": p.get("id"), "name": p.get("name") or p.get("payee_text"), "type": p.get("txn_type"),
        "amount": amount, "currency": currency,
        "account": p.get("outflow_account_name") or p.get("inflow_account_name"),
        "category": p.get("category_key"), "freq": p.get("freq"), "next_date": p.get("next_date"),
        "auto_post": p.get("auto_post"), "active": p.get("active"),
    }.items() if v is not None}


def compact_approval(a: dict) -> dict:
    return {k: v for k, v in {
        "id": a.get("id"), "action": a.get("action"), "status": a.get("status"),
        "requested_by": a.get("requested_by_name") or a.get("requested_by"),
        "created_at": a.get("created_at"), "note": a.get("note"),
        "transaction": {"id": a.get("target_id"), "date": a.get("txn_date"), "payee": a.get("payee_text"),
                        "amount": a.get("amount"), "currency": a.get("asset_code"),
                        "account": a.get("account_name")},
        "change": a.get("payload") or None,
    }.items() if v is not None}


def txn_body(kind: str, amount: float, account_id: str, *, txn_date: date,
             to_account_id: str | None = None, to_amount: float | None = None,
             category_key: str | None = None, payee: str | None = None,
             note: str | None = None, person_id: str | None = None,
             tags: list[str] | None = None) -> dict:
    """Turn (kind, amount, account) into the ledger's two-leg row."""
    if kind not in TXN_KINDS:
        raise ToolError(f"kind must be one of {', '.join(TXN_KINDS)}, got {kind!r}")
    if amount is None or amount <= 0:
        raise ToolError("amount must be a positive number in the account's currency")
    account_id = require_id("account_id", account_id)
    body: dict[str, Any] = {"txn_date": txn_date.isoformat(), "category_key": category_key,
                            "payee_text": payee, "note": note, "person_id": person_id,
                            "tags": tags or []}
    if kind == "expense":
        body.update(outflow_account_id=account_id, outflow_amount=amount)
    elif kind == "income":
        body.update(inflow_account_id=account_id, inflow_amount=amount)
    else:
        if not to_account_id:
            raise ToolError("a transfer needs to_account_id (the receiving account)")
        to_account_id = require_id("to_account_id", to_account_id)
        if to_account_id == account_id:
            raise ToolError("a transfer needs two different accounts")
        body.update(outflow_account_id=account_id, outflow_amount=amount,
                    inflow_account_id=to_account_id,
                    inflow_amount=to_amount if to_amount is not None else amount)
    return _clean(body) or {}


def amount_patch(current: dict, amount: float) -> dict:
    """Which leg a new amount applies to on an existing row."""
    if amount <= 0:
        raise ToolError("amount must be a positive number")
    if current.get("txn_type") == "transfer":
        raise ToolError("change a transfer's amounts in the app — both legs are involved")
    if current.get("outflow_account_id"):
        return {"outflow_amount": amount}
    return {"inflow_amount": amount}


def _iso(name: str, value: str | None, default: date) -> date:
    if not value:
        return default
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise ToolError(f"{name} must be an ISO date (2026-10-28), got {value!r}") from None


# --- tools -----------------------------------------------------------------

def register(mcp: FastMCP, api: MergeAPI, today: Callable[[], date]) -> None:

    @mcp.tool(annotations=READ)
    async def budget_status(month: str | None = None) -> dict[str, Any]:
        """This month's budgets vs actual spending per category, in USD:
        limit, spent, left, used %, and which categories are over.
        month is 2026-10; default the current month."""
        first, last = month_range(month, today())
        rows = await api.get("/api/finance/budgets", month=first.isoformat())
        return budget_status_view(rows, first, last)

    @mcp.tool(annotations=READ)
    async def spending_report(date_from: str | None = None, date_to: str | None = None) -> dict[str, Any]:
        """Spending by category for a period (USD), biggest first. Default:
        the current month to date. Dates are ISO."""
        t = today()
        d_from = _iso("date_from", date_from, t.replace(day=1))
        d_to = _iso("date_to", date_to, t)
        if d_to < d_from:
            raise ToolError("date_to is before date_from")
        rows = await api.get("/api/finance/reports/spending",
                             date_from=d_from.isoformat(), date_to=d_to.isoformat())
        cats = sorted(({"category": r.get("category_key"), "label": r.get("label"),
                        "usd": round(float(r.get("usd_total") or 0), 2),
                        "transactions": r.get("txn_count")} for r in rows),
                      key=lambda x: -x["usd"])
        return {"from": d_from.isoformat(), "to": d_to.isoformat(),
                "total_usd": round(sum(c["usd"] for c in cats), 2), "by_category": cats}

    @mcp.tool(annotations=READ)
    async def cashflow(months: int = 6) -> list[dict[str, Any]]:
        """Income vs expense per month (USD) for the last N months, oldest
        first, with the net."""
        rows = await api.get("/api/finance/reports/cashflow", months=max(1, min(months, 36)))
        out = []
        for r in sorted(rows, key=lambda x: x.get("month") or ""):
            inc = round(float(r.get("income") or 0), 2)
            exp = round(float(r.get("expense") or 0), 2)
            out.append({"month": r.get("month"), "income_usd": inc, "expense_usd": exp,
                        "net_usd": round(inc - exp, 2)})
        return out

    @mcp.tool(annotations=READ)
    async def list_transactions(q: str | None = None, category_key: str | None = None,
                                account_id: str | None = None, txn_type: str | None = None,
                                date_from: str | None = None, date_to: str | None = None,
                                limit: int = 50) -> list[dict[str, Any]]:
        """Ledger rows, newest first. q searches payee and note; txn_type is
        expense | income | transfer; category keys come from list_categories,
        account ids from list_accounts. Amounts are in the account currency."""
        if txn_type and txn_type not in TXN_KINDS:
            raise ToolError(f"txn_type must be one of {', '.join(TXN_KINDS)}")
        rows = await api.get("/api/finance/transactions", q=q, category_key=category_key,
                             account_id=require_id("account_id", account_id) if account_id else None,
                             txn_type=txn_type, date_from=date_from, date_to=date_to,
                             limit=max(1, min(limit, MAX_TXNS)))
        ids = await sensitive_ids()
        return [compact_txn(t, ids) for t in rows]

    @mcp.tool(annotations=READ)
    async def list_accounts(include_archived: bool = False) -> list[dict[str, Any]]:
        """Accounts (bank, cash, wallets, exchanges, brokerage, debts) with
        owner, currency and current balances. Use the ids when booking."""
        rows = await api.get("/api/finance/accounts", include_archived=include_archived)
        return [compact_account(a) for a in rows]

    @mcp.tool(annotations=READ)
    async def list_categories() -> list[dict[str, Any]]:
        """Spending/income categories: key (what the other tools take),
        label, kind, parent."""
        rows = await api.get("/api/finance/categories")
        return [{k: v for k, v in {"key": c.get("key"), "label": c.get("label"), "kind": c.get("kind"),
                                   "parent": c.get("parent_key"), "in_use": c.get("in_use")}.items()
                 if v is not None} for c in rows]

    @mcp.tool(annotations=READ)
    async def net_worth() -> dict[str, Any]:
        """Net worth now: totals in USD and THB, operational vs investment,
        by asset group and by owner, and the largest asset lines."""
        nw = await api.get("/api/finance/net-worth")
        lines = sorted(({"currency": a.get("asset_code"), "balance": a.get("balance"),
                         "usd_value": a.get("usd_value")} for a in nw.get("by_asset") or []),
                       key=lambda a: -float(a["usd_value"] or 0))
        return {"total_usd": nw.get("total_usd"), "total_thb": nw.get("total_thb"),
                "operational_usd": nw.get("operational_usd"), "investment_usd": nw.get("investment_usd"),
                "usd_thb_rate": nw.get("usd_thb_rate"),
                "by_group": nw.get("by_group") or [], "by_owner": nw.get("by_owner") or [],
                "top_assets": lines[:MAX_ASSET_LINES]}

    @mcp.tool(annotations=READ)
    async def list_planned() -> list[dict[str, Any]]:
        """Recurring and planned payments (rent, subscriptions, salaries)
        with the next due date; post_planned books one now."""
        rows = await api.get("/api/finance/planned")
        return sorted((compact_planned(p) for p in rows), key=lambda p: p.get("next_date") or "")

    @mcp.tool(annotations=READ)
    async def pending_approvals() -> list[dict[str, Any]]:
        """Changes a family member asked for on the shared ledger (edit or
        delete of a transaction) that wait for the owner's decision."""
        rows = await api.get("/api/finance/approvals", status_filter="pending")
        return [compact_approval(a) for a in rows]

    # --- writes ---

    @mcp.tool(annotations=WRITE)
    async def add_transaction(kind: str, amount: float, account_id: str,
                              txn_date: str | None = None, category_key: str | None = None,
                              payee: str | None = None, note: str | None = None,
                              to_account_id: str | None = None, to_amount: float | None = None,
                              person_id: str | None = None,
                              tags: list[str] | None = None) -> dict[str, Any]:
        """Book a transaction. kind is expense | income | transfer; amount is
        in the account's currency (positive). A transfer also needs
        to_account_id, and to_amount when the currencies differ. Default date
        is today. Pick account ids from list_accounts and category keys from
        list_categories; say the payee as the owner would."""
        if person_id:
            person_id = require_id("person_id", person_id)
            if person_id in await sensitive_ids():
                raise ToolError(sens.NOTE + " — do this in the app")
        body = txn_body(kind, amount, account_id, txn_date=_iso("txn_date", txn_date, today()),
                        to_account_id=to_account_id, to_amount=to_amount, category_key=category_key,
                        payee=payee, note=note, person_id=person_id, tags=tags)
        return compact_txn(await api.post("/api/finance/transactions", json=body), await sensitive_ids())

    @mcp.tool(annotations=WRITE)
    async def update_transaction(txn_id: str, category_key: str | None = None,
                                 payee: str | None = None, note: str | None = None,
                                 txn_date: str | None = None, amount: float | None = None,
                                 tags: list[str] | None = None) -> dict[str, Any]:
        """Fix a booked row: category, payee, note, date, amount or tags.
        Only the fields given change. Deleting is done in the app."""
        txn_id = require_id("txn_id", txn_id)
        body = _clean({"category_key": category_key, "payee_text": payee, "note": note,
                       "txn_date": _iso("txn_date", txn_date, today()).isoformat() if txn_date else None,
                       "tags": tags}) or {}
        if amount is not None:
            body.update(amount_patch(await api.get(f"/api/finance/transactions/{txn_id}"), amount))
        if not body:
            raise ToolError("nothing to change")
        return compact_txn(await api.patch(f"/api/finance/transactions/{txn_id}", json=body),
                           await sensitive_ids())

    @mcp.tool(annotations=WRITE)
    async def set_budget(category_key: str, limit_usd: float) -> dict[str, Any]:
        """Set (or change) the monthly USD limit for a category."""
        if limit_usd < 0:
            raise ToolError("limit_usd must be zero or more")
        if not (category_key or "").strip():
            raise ToolError("category_key is required (see list_categories)")
        return compact_budget(await api.call("PUT", "/api/finance/budgets",
                                             json={"category_key": category_key.strip(),
                                                   "limit_usd": limit_usd}))

    @mcp.tool(annotations=WRITE)
    async def post_planned(planned_id: str) -> dict[str, Any]:
        """Book a planned payment's current occurrence now and move its
        next date forward."""
        planned_id = require_id("planned_id", planned_id)
        return compact_planned(await api.post(f"/api/finance/planned/{planned_id}/post"))

    @mcp.tool(annotations=WRITE)
    async def decide_approval(approval_id: str, approve: bool, note: str | None = None) -> dict[str, Any]:
        """Approve or reject a family member's requested change. Only after
        the owner has said which way."""
        approval_id = require_id("approval_id", approval_id)
        verb = "approve" if approve else "reject"
        return compact_approval(await api.post(f"/api/finance/approvals/{approval_id}/{verb}",
                                               json={"note": note} if note else None))
