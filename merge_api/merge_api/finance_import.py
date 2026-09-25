"""Finance imports: ZenMoney migration + PDF/CSV bank-statement parsing.

Kept out of app.py/queries.py so those stay readable. Two independent paths:

- ZenMoney: pull the v8 diff (full dump), map instruments/accounts/tags/
  merchants/transactions into the fin_* tables, idempotent on
  (source_kind='zenmoney', source_ref=<zen id>) so re-runs sync rather than
  duplicate. Seeds USD FX from ZenMoney's RUB-based instrument rates.
- Statement: extract text from a PDF (pypdf) or read a CSV, hand it to the
  Anthropic tool-use classifier (extraction.py pattern) to produce reviewable
  rows that land in a fin_import_batch; commit creates fin_transactions.
"""
from __future__ import annotations

import csv
import io
import json
import logging
from collections import Counter
from datetime import date, datetime

import asyncpg

from . import queries

log = logging.getLogger("finance_import")

ZEN_DIFF_URL = "https://api.zenmoney.ru/v8/diff/"

# crypto codes that may appear as ZenMoney instruments (treated as kind=crypto)
_CRYPTO_CODES = {"BTC", "ETH", "USDT", "USDC", "SOL", "TRX", "BNB", "TON", "XRP", "DOGE"}

# ZenMoney account.type → our fin_account.kind
_ZEN_ACCT_KIND = {
    "ccard": "bank", "checking": "bank", "emoney": "bank", "deposit": "bank",
    "cash": "cash", "loan": "debt", "debt": "debt",
}


class ZenMoneyError(Exception):
    pass


class ImportParseError(Exception):
    pass


# ============================================================
# ZenMoney
# ============================================================

async def fetch_zenmoney_diff(token: str) -> dict:
    import httpx
    body = {"currentClientTimestamp": int(datetime.utcnow().timestamp()), "serverTimestamp": 0}
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(ZEN_DIFF_URL, json=body, headers=headers)
    except httpx.HTTPError as e:
        raise ZenMoneyError(f"ZenMoney request failed: {e}") from e
    if r.status_code == 401:
        raise ZenMoneyError("ZenMoney token rejected (401) — check ZENMONEY_TOKEN")
    if r.status_code != 200:
        raise ZenMoneyError(f"ZenMoney returned {r.status_code}: {r.text[:200]}")
    return r.json()


async def sync_zenmoney(pool: asyncpg.Pool, token: str) -> dict:
    """Full idempotent import of a ZenMoney account into the fin_* tables.
    Returns counts. Safe to run repeatedly (upserts on zen ids)."""
    diff = await fetch_zenmoney_diff(token)
    instruments = diff.get("instrument") or []
    accounts = diff.get("account") or []
    tags = diff.get("tag") or []
    merchants = diff.get("merchant") or []
    transactions = diff.get("transaction") or []

    # zen instrument id → (rate in RUB per unit), and the USD anchor
    inst_by_id = {i["id"]: i for i in instruments}
    usd_zen = next((i for i in instruments if (i.get("shortTitle") or "").upper() == "USD"), None)
    usd_rub_rate = float(usd_zen["rate"]) if usd_zen and usd_zen.get("rate") else None

    async with pool.acquire() as conn:
        async with conn.transaction():
            # --- instruments → fin_asset (+ seed USD FX) ---
            asset_id_by_zen: dict[int, str] = {}
            today = date.today()
            for i in instruments:
                code = (i.get("shortTitle") or "").upper()
                if not code:
                    continue
                kind = "crypto" if code in _CRYPTO_CODES else "fiat"
                decimals = 8 if kind == "crypto" else 2
                # Match on (code, chain) — seeded fiat/crypto assets already
                # occupy that slot with a NULL zen id; link the zen id onto them.
                row = await conn.fetchrow(
                    """
                    INSERT INTO memory.fin_asset (code, name, kind, decimals, symbol, chain, zen_instrument_id)
                    VALUES ($1, $2, $3, $4, $5, NULL, $6)
                    ON CONFLICT (code, COALESCE(chain, ''))
                    DO UPDATE SET zen_instrument_id = excluded.zen_instrument_id,
                                  name = COALESCE(memory.fin_asset.name, excluded.name),
                                  symbol = COALESCE(memory.fin_asset.symbol, excluded.symbol)
                    RETURNING id::text
                    """,
                    code, i.get("title"), kind, decimals, i.get("symbol"), i["id"],
                )
                asset_id_by_zen[i["id"]] = row["id"]
                # seed USD rate from ZenMoney RUB-based rate: usd = rate_rub / usd_rub
                if usd_rub_rate and i.get("rate"):
                    usd_rate = float(i["rate"]) / usd_rub_rate
                    await conn.execute(
                        """
                        INSERT INTO memory.fin_fx_rate (asset_id, quote, rate, rate_date, source)
                        VALUES ($1::uuid, 'USD', $2, $3, 'zenmoney')
                        ON CONFLICT (asset_id, quote, rate_date)
                        DO UPDATE SET rate = excluded.rate, source = 'zenmoney'
                        """, row["id"], usd_rate, today)

            # --- accounts → fin_account ---
            acct_id_by_zen: dict[str, str] = {}
            for a in accounts:
                kind = _ZEN_ACCT_KIND.get(a.get("type"), "bank")
                cur_asset = asset_id_by_zen.get(a.get("instrument"))
                row = await conn.fetchrow(
                    """
                    INSERT INTO memory.fin_account
                      (name, kind, currency_asset_id, owner, opening_balance,
                       include_in_net_worth, archived, zen_user_id, source_kind, source_ref)
                    VALUES ($1, $2, $3::uuid, 'me', $4, $5, $6, $7, 'zenmoney', $8)
                    ON CONFLICT (source_kind, source_ref) WHERE source_ref IS NOT NULL
                    DO UPDATE SET name = excluded.name, kind = excluded.kind,
                                  currency_asset_id = excluded.currency_asset_id,
                                  opening_balance = excluded.opening_balance,
                                  include_in_net_worth = excluded.include_in_net_worth,
                                  archived = excluded.archived
                    RETURNING id::text
                    """,
                    a.get("title") or "Account", kind, cur_asset,
                    a.get("startBalance") or 0, bool(a.get("inBalance")),
                    bool(a.get("archive")), str(a.get("user") or ""), str(a["id"]),
                )
                acct_id_by_zen[str(a["id"])] = row["id"]

            # --- tags → fin_category (two-pass for the parent FK) ---
            for t in tags:
                show_in = t.get("showIncome"); show_out = t.get("showOutcome")
                kind = "both" if (show_in and show_out) else ("income" if show_in else "expense")
                await conn.execute(
                    """
                    INSERT INTO memory.fin_category (key, label, kind, color, icon, source_kind, source_ref)
                    VALUES ($1, $2, $3, 'slate', $4, 'zenmoney', $1)
                    ON CONFLICT (key) DO UPDATE SET label = excluded.label, kind = excluded.kind
                    """,
                    str(t["id"]), t.get("title") or "(tag)", kind, t.get("icon"),
                )
            for t in tags:  # second pass: wire parents now that all keys exist
                if t.get("parent"):
                    await conn.execute(
                        "UPDATE memory.fin_category SET parent_key = $2 WHERE key = $1",
                        str(t["id"]), str(t["parent"]))

            # --- merchants → fin_payee ---
            payee_id_by_zen: dict[str, str] = {}
            for m in merchants:
                row = await conn.fetchrow(
                    """
                    INSERT INTO memory.fin_payee (name, source_kind, source_ref)
                    VALUES ($1, 'zenmoney', $2)
                    ON CONFLICT (source_kind, source_ref) WHERE source_ref IS NOT NULL
                    DO UPDATE SET name = excluded.name
                    RETURNING id::text
                    """, m.get("title") or "(payee)", str(m["id"]))
                payee_id_by_zen[str(m["id"])] = row["id"]

            # --- transactions → fin_transaction ---
            n_txn = 0
            for tx in transactions:
                if tx.get("deleted"):
                    continue
                income = tx.get("income") or 0
                outcome = tx.get("outcome") or 0
                if not income and not outcome:
                    continue
                out_acct = acct_id_by_zen.get(str(tx.get("outcomeAccount"))) if outcome else None
                in_acct = acct_id_by_zen.get(str(tx.get("incomeAccount"))) if income else None
                if out_acct is None and in_acct is None:
                    continue
                out_asset = asset_id_by_zen.get(tx.get("outcomeInstrument")) if outcome else None
                in_asset = asset_id_by_zen.get(tx.get("incomeInstrument")) if income else None
                tag_list = tx.get("tag") or []
                category_key = str(tag_list[0]) if tag_list else None
                payee_id = payee_id_by_zen.get(str(tx.get("merchant"))) if tx.get("merchant") else None
                try:
                    txn_date = date.fromisoformat(str(tx.get("date"))[:10])
                except (ValueError, TypeError):
                    continue
                await conn.execute(
                    """
                    INSERT INTO memory.fin_transaction
                      (txn_date, outflow_account_id, outflow_asset_id, outflow_amount,
                       inflow_account_id, inflow_asset_id, inflow_amount,
                       category_key, payee_id, payee_text, note, source_kind, source_ref)
                    VALUES ($1::date, $2::uuid, $3::uuid, $4, $5::uuid, $6::uuid, $7,
                            $8, $9::uuid, $10, $11, 'zenmoney', $12)
                    ON CONFLICT (source_kind, source_ref) WHERE source_ref IS NOT NULL
                    DO UPDATE SET txn_date = excluded.txn_date,
                                  outflow_account_id = excluded.outflow_account_id,
                                  outflow_asset_id = excluded.outflow_asset_id,
                                  outflow_amount = excluded.outflow_amount,
                                  inflow_account_id = excluded.inflow_account_id,
                                  inflow_asset_id = excluded.inflow_asset_id,
                                  inflow_amount = excluded.inflow_amount,
                                  category_key = excluded.category_key,
                                  payee_id = excluded.payee_id, note = excluded.note,
                                  deleted_at = NULL
                    """,
                    txn_date,
                    out_acct, out_asset, (outcome or None) if out_acct else None,
                    in_acct, in_asset, (income or None) if in_acct else None,
                    _category_or_null(category_key, tag_list), payee_id,
                    tx.get("payee"), tx.get("comment"), str(tx["id"]),
                )
                n_txn += 1

    return {
        "instruments": len(asset_id_by_zen),
        "accounts": len(acct_id_by_zen),
        "categories": len(tags),
        "payees": len(payee_id_by_zen),
        "transactions": n_txn,
    }


def _category_or_null(key: str | None, tag_list: list) -> str | None:
    # ZenMoney "null tag" sentinel ids exist; only keep a key that looks real
    if not key:
        return None
    return key


# ============================================================
# PDF / CSV statement parsing
# ============================================================

_STATEMENT_TOOL = {
    "name": "record_statement",
    "description": "Extract every transaction row from a bank/financial statement.",
    "input_schema": {
        "type": "object",
        "properties": {
            "currency": {"type": "string", "description": "ISO code of the statement currency if shown, e.g. THB"},
            "rows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "date": {"type": "string", "description": "ISO date YYYY-MM-DD"},
                        "amount": {"type": "number", "description": "absolute amount, positive"},
                        "direction": {"type": "string", "enum": ["debit", "credit"],
                                       "description": "debit = money out, credit = money in"},
                        "description": {"type": "string"},
                        "balance": {"type": ["number", "null"], "description": "running balance if shown"},
                    },
                    "required": ["date", "amount", "direction", "description"],
                },
            },
        },
        "required": ["rows"],
    },
}

_STATEMENT_SYSTEM = (
    "You extract transactions from bank or financial statements (any bank, any language — "
    "Thai, Russian, English). The text is column-aligned (spaces preserve columns).\n"
    "AMOUNT + DIRECTION — statements use one of two layouts:\n"
    "  (a) TWO amount columns: a value in the debit/withdrawal column → direction=debit "
    "(money OUT); a value in the credit/deposit column → direction=credit (money IN).\n"
    "  (b) ONE signed amount column: a leading '+' means credit (money IN); no sign, or a "
    "leading '-'/'−', means debit (money OUT). Russian banks (Sber/Сбербанк) use this: "
    "'+25 000,00' is money IN, '25 000,00' is money OUT.\n"
    "RUNNING BALANCE is the strongest signal and most rows show it (the number that trends "
    "up and down across rows). If the balance went UP after a row it is a credit; if it went "
    "DOWN it is a debit. Use it to decide direction whenever present, and always report it in "
    "`balance`. NEVER report the running balance as the amount; report `amount` as a positive "
    "number.\n"
    "Language cues — Russian: 'Перевод на карту', 'Перевод от', 'Пополнение', 'Возврат', "
    "'Зачисление' → credit; 'Перевод с карты', 'Перевод для', 'Оплата', 'Покупка', "
    "'Прочие расходы', 'Списание', 'Комиссия' → debit. Thai: 'จ่ายบิล'/'Transfer to'/'ATM'/'ถอน' "
    "→ debit; 'รับโอนจาก'/'รับโอน'/'เงินเข้า' → credit.\n"
    "Use the description/memo column (merchant or payee) as the description. Ignore headers, "
    "opening/closing/brought-forward balance lines, per-period totals (ИТОГО, Пополнение, "
    "Списание, Остаток) and summaries. Extract EVERY transaction row; omit one only if its "
    "amount is genuinely unreadable."
)


def _pdf_text(content: bytes) -> str:
    """Bank statements are columnar (date · debit · credit · balance · memo); the
    debit-vs-credit distinction is POSITIONAL, so column alignment must survive.
    pdfplumber's layout mode preserves it best; fall back to pypdf if it fails."""
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            txt = "\n".join((pg.extract_text(layout=True) or "") for pg in pdf.pages)
        if len(txt.strip()) >= 40:
            return txt.strip()
    except Exception:  # noqa: BLE001
        log.warning("pdfplumber failed, falling back to pypdf", exc_info=True)
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(content))
    return "\n".join((p.extract_text() or "") for p in reader.pages).strip()


def _csv_rows(content: bytes) -> dict:
    """Best-effort CSV parse without an LLM: find date/amount/description columns."""
    text = content.decode("utf-8", "replace")
    sniff = csv.Sniffer()
    try:
        dialect = sniff.sniff(text[:2000])
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    rows = []
    for r in reader:
        low = { (k or "").strip().lower(): (v or "").strip() for k, v in r.items() }
        d = _first(low, ("date", "transaction date", "дата", "วันที่"))
        amt = _first(low, ("amount", "сумма", "value", "จำนวน"))
        desc = _first(low, ("description", "details", "narrative", "назначение", "payee", "memo"))
        if not (d and amt):
            continue
        try:
            num = float(amt.replace(",", "").replace(" ", ""))
        except ValueError:
            continue
        rows.append({
            "date": _norm_date(d),
            "amount": abs(num),
            "direction": "credit" if num > 0 else "debit",
            "description": desc or "",
            "balance": None,
        })
    return {"kind": "csv", "currency": None, "rows": rows}


def _first(d: dict, keys: tuple) -> str | None:
    for k in keys:
        if k in d and d[k]:
            return d[k]
    return None


def _norm_date(s: str) -> str:
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d.%m.%Y", "%d-%m-%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return s  # leave as-is; review UI surfaces it


def _reconcile_directions(rows: list[dict]) -> list[dict]:
    """When a statement prints a running balance per row, direction is provable:
    a row is a credit iff it raised the balance. Single-signed-column statements
    (e.g. Sber: '+25 000,00' in, '25 000,00' out) trip the two-column heuristic,
    so we correct each row's direction from the balance MOVEMENT wherever we can
    prove it — the step between a row's balance and an adjacent row's balance
    equals the row's amount. Rows whose balance can't be tied to a neighbour keep
    the model's direction, so this only ever overrides on hard evidence."""
    def _num(x):
        return float(x) if isinstance(x, (int, float)) else None

    n = len(rows)
    bals = [_num(r.get("balance")) for r in rows]
    if sum(b is not None for b in bals) < max(3, n * 0.6):
        return rows  # too few running balances to trust — leave the model's call
    TOL = 0.02
    for i, r in enumerate(rows):
        b, amt = bals[i], _num(r.get("amount"))
        if b is None or not amt:
            continue
        # The neighbour whose balance differs from this row's by ~amount is the
        # balance BEFORE this transaction (order-agnostic: try either side).
        for j in (i + 1, i - 1):
            if 0 <= j < n and bals[j] is not None and abs(abs(b - bals[j]) - amt) <= TOL:
                r["direction"] = "credit" if b > bals[j] else "debit"
                break
    return rows


async def parse_statement(client, model: str, *, filename: str, content: bytes) -> dict:
    """Return {kind, currency, rows:[{date,amount,direction,description,balance}]}."""
    if filename.lower().endswith(".csv"):
        out = _csv_rows(content)
        if not out["rows"]:
            raise ImportParseError("no rows found in CSV — check the column headers")
        return out

    text = _pdf_text(content)
    if len(text.strip()) < 20:
        raise ImportParseError("could not extract text from this PDF (scanned image? OCR not supported yet)")
    # cap to keep token use sane; layout text is larger but tabular/cheap
    text = text[:120000]
    resp = await client.messages.create(
        model=model, max_tokens=16000, system=_STATEMENT_SYSTEM,
        tools=[_STATEMENT_TOOL],
        tool_choice={"type": "tool", "name": "record_statement"},
        messages=[{"role": "user", "content": f"Statement text:\n\n{text}"}],
    )
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "record_statement":
            data = dict(block.input)
            rows = data.get("rows") or []
            if not rows:
                raise ImportParseError("the parser found no transactions in this statement")
            rows = _reconcile_directions(rows)
            return {"kind": "pdf", "currency": data.get("currency"), "rows": rows}
    raise ImportParseError("the parser returned no result")


_ENRICH_TOOL = {
    "name": "map_statement_rows",
    "description": "Map each bank-statement memo to a category and a clean payee.",
    "input_schema": {
        "type": "object",
        "properties": {
            "rows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "i": {"type": "integer", "description": "the memo's row number"},
                        "category_key": {"type": "string", "description": "best-matching category key from the list, or empty"},
                        "payee": {"type": "string", "description": "clean merchant/person name if the memo names one, else empty"},
                    },
                    "required": ["i"],
                },
            },
        },
        "required": ["rows"],
    },
}

_ENRICH_SYSTEM = (
    "You categorize bank-statement transactions. For each raw bank memo (Thai or "
    "English), pick the SINGLE best category key from the provided list (or empty "
    "if none fits), and extract a clean payee/merchant name IF the memo names one "
    "(else empty). Generic memos — 'PMT FOR GOODS', 'PMT. PROMPTPAY', 'PURCHASE "
    "E-CHN', 'ATM', 'TRANSFER' — have no real payee: leave payee empty and pick a "
    "broad category (or none). Only use category keys that appear in the list."
)


async def enrich_statement_rows(client, model: str, descriptions: list[str],
                                categories: list[dict]) -> dict:
    """One LLM call: {description -> {category_key, payee}} for the unique memos."""
    if not client or not descriptions:
        return {}
    cat_list = "\n".join(
        f"{c['key']} = {c['label']} [{c['kind']}]" for c in categories
        if c.get("kind") in ("expense", "income", "both"))
    numbered = "\n".join(f"{i}. {d}" for i, d in enumerate(descriptions))
    try:
        resp = await client.messages.create(
            model=model, max_tokens=4000, system=_ENRICH_SYSTEM,
            tools=[_ENRICH_TOOL], tool_choice={"type": "tool", "name": "map_statement_rows"},
            messages=[{"role": "user", "content": f"Categories:\n{cat_list}\n\nMemos:\n{numbered}"}])
    except Exception as e:  # noqa: BLE001
        log.warning("statement enrichment failed (importing uncategorised): %s", e)
        return {}
    out: dict[str, dict] = {}
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "map_statement_rows":
            for m in dict(block.input).get("rows", []):
                i = m.get("i")
                if isinstance(i, int) and 0 <= i < len(descriptions):
                    out[descriptions[i]] = {
                        "category_key": (m.get("category_key") or "").strip() or None,
                        "payee": (m.get("payee") or "").strip(),
                    }
    return out


async def commit_statement_batch(pool: asyncpg.Pool, batch: dict, *, account_id: str,
                                 skip_indices: set[int], client=None, model: str | None = None) -> dict:
    """Create fin_transactions from a reviewed PDF/CSV batch, with:
    - content-based DEDUP (skip rows already on the account by date+amount+memo,
      so re-imports / overlapping statements don't duplicate),
    - LLM CATEGORIZATION (one call maps unique memos → category),
    - PAYEE MATCHING to existing fin_payee rows.
    Returns {created, skipped}."""
    parsed = batch.get("parsed") or {}
    rows = parsed.get("rows") or []
    src_kind = "csv_import" if batch.get("kind") == "csv" else "pdf_import"

    todo = []
    for idx, r in enumerate(rows):
        if idx in skip_indices:
            continue
        try:
            d = date.fromisoformat(r["date"])
        except (ValueError, KeyError, TypeError):
            continue
        amount = abs(float(r.get("amount") or 0))
        if amount == 0:
            continue
        todo.append((idx, d, amount, r.get("direction") == "debit", (r.get("description") or "")))

    # categorize + clean-payee the unique memos in one LLM pass
    cats = await queries.list_fin_categories(pool)
    valid_keys = {c["key"] for c in cats}
    uniq = list({t[4] for t in todo if t[4]})[:250]
    enrich = await enrich_statement_rows(client, model, uniq, cats) if (client and model) else {}

    async with pool.acquire() as conn:
        cur_asset = await conn.fetchval(
            "SELECT currency_asset_id::text FROM memory.fin_account WHERE id = $1::uuid", account_id)
        # Fingerprint multiset of transactions already on this account (ANY source).
        # Key = (date, amount, direction) RELATIVE TO THIS ACCOUNT. We deliberately
        # exclude the description: the same transaction carries different memos
        # across sources (a ZenMoney label vs a bank-statement line), so including
        # it would defeat cross-source dedup. Direction/amount are taken from this
        # account's leg so a transfer dedupes against a single-leg statement row.
        existing = await conn.fetch(
            """
            SELECT txn_date,
                   (CASE WHEN outflow_account_id = $1::uuid THEN outflow_amount
                         ELSE inflow_amount END)::float8 AS amt,
                   -- COALESCE: for an inflow-only row outflow_account_id is NULL,
                   -- and `NULL = uuid` is NULL (not FALSE) — which would stop a
                   -- credit row from ever matching. Force it to FALSE.
                   COALESCE(outflow_account_id = $1::uuid, FALSE) AS is_debit
              FROM memory.fin_transaction
             WHERE deleted_at IS NULL
               AND (outflow_account_id = $1::uuid OR inflow_account_id = $1::uuid)
            """, account_id)
        seen = Counter((r["txn_date"], round(r["amt"] or 0, 2), r["is_debit"])
                       for r in existing)

        payee_cache: dict[str, str | None] = {}

        async def match_payee(name: str) -> str | None:
            if not name:
                return None
            k = name.lower()
            if k in payee_cache:
                return payee_cache[k]
            pid = await conn.fetchval(
                """
                SELECT id::text FROM memory.fin_payee
                 WHERE name ILIKE $1 OR similarity(name, $1) > 0.45
                 ORDER BY (name ILIKE $1) DESC, similarity(name, $1) DESC LIMIT 1
                """, name)
            payee_cache[k] = pid
            return pid

        created = skipped = 0
        for idx, d, amount, debit, descr in todo:
            fp = (d, round(amount, 2), debit)
            if seen[fp] > 0:                 # already present → genuine duplicate
                seen[fp] -= 1
                skipped += 1
                continue
            info = enrich.get(descr) or {}
            cat = info.get("category_key")
            if cat not in valid_keys:
                cat = None
            clean_payee = info.get("payee") or ""
            payee_id = await match_payee(clean_payee) if clean_payee else None
            payee_text = clean_payee or descr[:300]
            ref = f"{batch['id']}:{idx}"
            await conn.execute(
                """
                INSERT INTO memory.fin_transaction
                  (txn_date, outflow_account_id, outflow_asset_id, outflow_amount,
                   inflow_account_id, inflow_asset_id, inflow_amount,
                   category_key, payee_id, payee_text, note, import_batch_id, source_kind, source_ref)
                VALUES ($1::date,
                        CASE WHEN $2 THEN $3::uuid ELSE NULL END, CASE WHEN $2 THEN $4::uuid ELSE NULL END,
                        CASE WHEN $2 THEN $5::numeric ELSE NULL END,
                        CASE WHEN $2 THEN NULL ELSE $3::uuid END, CASE WHEN $2 THEN NULL ELSE $4::uuid END,
                        CASE WHEN $2 THEN NULL ELSE $5::numeric END,
                        $6, $7::uuid, $8, $9, $10::uuid, $11, $12)
                ON CONFLICT (source_kind, source_ref) WHERE source_ref IS NOT NULL DO NOTHING
                """,
                d, debit, account_id, cur_asset, amount,
                cat, payee_id, payee_text, descr, batch["id"], src_kind, ref,
            )
            created += 1
    return {"created": created, "skipped": skipped}
