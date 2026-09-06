"""Auto-categorize the finance backlog by LEARNING from history.

The user's ~8.7k ZenMoney transactions are already categorized — that IS how
they categorize. So instead of asking a model to guess, we build a
memo/payee → category index from that history and apply it to the
uncategorized rows deterministically (a recurring merchant gets the same
category the user always gave it). Only the genuinely-new memos fall through
to the local LLM (Phase 3).

Self / own-account transfers ("Transfer to KBNK x1420 MR SMITH", "→ MRS
SMITH", K PLUS internal moves) are NOT spending — they're detected here and
handed to the leg-pairing pass (Phase 2) rather than given a category.

This module is pure/deterministic and dry-runnable; the orchestrator reports
what it WOULD do before anything is written.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections import Counter, defaultdict
from typing import Any

log = logging.getLogger("merge_api.finance_categorize")


def _build_self_xfer() -> re.Pattern:
    """Recognize the account owner (+ household) on bank-slip memos, so a
    "transfer to <owner>" is an own-account move, not spending. The names are
    configured via FINANCE_SELF_NAMES — a case-insensitive regex ALTERNATION
    of the name fragments as they print on your statements, e.g.
        FINANCE_SELF_NAMES='smith|m(?:r|ister)\\.? *john|mrs?\\.? *jane'
    Empty/unset → name-based self-transfer detection is OFF (a fresh install
    turns it on by setting the env). A bad regex falls back to OFF with a warn,
    never a crash — finance categorization must not break on a config typo."""
    raw = (os.environ.get("FINANCE_SELF_NAMES") or "").strip()
    if not raw:
        return re.compile(r"(?!)")   # matches nothing
    try:
        return re.compile(f"({raw})", re.IGNORECASE)
    except re.error as e:
        log.warning("FINANCE_SELF_NAMES is not a valid regex (%s) — "
                    "self-transfer name detection disabled", e)
        return re.compile(r"(?!)")


_SELF_XFER = _build_self_xfer()

# generic bank memos with no real merchant/payee — never make a payee from these.
_GENERIC = re.compile(
    r"^(pmt\.?|payment|purchase|trf\.?|transfer|atm|prompt ?pay|pmt for goods|"
    r"purchase e-chn|e-chn|deposit|withdrawal|fee|interest|refund)s?\b",
    re.IGNORECASE,
)
_CRYPTO_ADDR = re.compile(r"^(0x[0-9a-f]{2,}|[a-z0-9…]{6,})[.…]", re.IGNORECASE)


def is_enrichable(memo: str | None) -> bool:
    """A memo worth sending to the LLM: has content, names something specific
    (not a generic bank op or a raw wallet address)."""
    if not memo or len(memo.strip()) < 3:
        return False
    m = memo.strip()
    if _CRYPTO_ADDR.match(m) or m.startswith("0x"):
        return False
    return not _GENERIC.match(m)

# tokens stripped from a memo before matching: card/account tails, standalone
# digit runs, and boilerplate that varies per transaction but not per merchant.
_NOISE = re.compile(
    r"\b(x\d{3,}|\d{4,}|ref\w*|rrn\w*|no\.?\d+|pos|e-chn|prompt ?pay|"
    r"rus|tha|usa|bkk|bangkok)\b",
    re.IGNORECASE,
)
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_WS = re.compile(r"\s+")


def norm_memo(text: str | None) -> str:
    """Normalize a payee/memo to a stable merchant key for matching."""
    if not text:
        return ""
    s = _NOISE.sub(" ", text.lower())
    s = _PUNCT.sub(" ", s)
    s = _WS.sub(" ", s).strip()
    return s


def is_self_transfer(payee_text: str | None, note: str | None = None) -> bool:
    """True if the memo marks a move between own/family accounts (not spending)."""
    return bool(_SELF_XFER.search(f"{payee_text or ''} {note or ''}"))


# Known merchants → (clean payee, default expense category label). Thai bank
# slips print the merchant messily ("TOPS-C2B your city Festival", "BTM(THAILAND)
# LTD.", "GRABTAXI (", "เพ็ท คลับ-บูกิส ภูเก็ต"), so we match on a regex and
# normalise to a tidy payee + a sensible default category. FIRST match wins —
# the Grab taxi/rides carve-out is listed before the food-delivery default.
# Category labels are matched against fin_category by the existing fuzzy
# resolver, so they stay portable (no hard-coded ids).
_MERCHANTS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"grab\s*(?:taxi|rides?)", re.I), "Grab", "Taxi"),
    (re.compile(r"\bgrab\b|www\.grab\.com", re.I), "Grab", "Food Delivery"),
    (re.compile(r"\btops\b", re.I), "Tops", "Groceries"),
    (re.compile(r"\bbtm\b|bread\s*talk", re.I), "Bread Talk", "Groceries"),
    (re.compile(r"yamazaki", re.I), "Thai Yamazaki", "Groceries"),
    (re.compile(r"pet\s*club|เพ็ท\s*คลับ", re.I), "Pet Club", "Pets"),
    # Bangchak = a Thai gas-station brand; vision-reads of the Thai บางจาก mangle
    # it ("Bangkak … School" → a bogus Education category), so pin it here.
    (re.compile(r"บางจาก|bang\s?[ck]h?ak", re.I), "Bangchak", "Gas"),
]


def merchant_default(text: str | None) -> tuple[str, str] | None:
    """Map a messy slip memo to a known merchant → (clean payee, default
    category label), or None if it's not a merchant we recognise."""
    s = (text or "").strip()
    if not s:
        return None
    for pat, payee, cat in _MERCHANTS:
        if pat.search(s):
            return payee, cat
    return None


def build_history_index(rows: list[dict]) -> dict[str, dict[str, Any]]:
    """From CATEGORIZED history rows [{category_key, payee_id, payee_text}], build
    lookup indexes: by resolved payee_id (strongest) and by normalized memo. Each
    key → {category_key, count, share} for the dominant category, so a caller can
    gate on confidence (share)."""
    by_pid: dict[str, Counter] = defaultdict(Counter)
    by_memo: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        cat = r.get("category_key")
        if not cat:
            continue
        pid = r.get("payee_id")
        if pid:
            by_pid[str(pid)][cat] += 1
        memo = norm_memo(r.get("payee_text"))
        if memo:
            by_memo[memo][cat] += 1

    def _dominant(table: dict[str, Counter]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for key, counter in table.items():
            total = sum(counter.values())
            cat, n = counter.most_common(1)[0]
            out[key] = {"category_key": cat, "count": n, "share": n / total}
        return out

    return {"by_payee_id": _dominant(by_pid), "by_memo": _dominant(by_memo)}


def classify(row: dict, index: dict[str, dict[str, Any]], *, min_share: float = 0.6,
             min_count: int = 1) -> dict[str, Any]:
    """Decide what to do with one uncategorized row. Returns one of:
      {action:'self_transfer'}                         — hand to leg-pairing
      {action:'history', category_key, source, confidence} — apply (high conf)
      {action:'none'}                                  — leave for the LLM pass
    """
    if is_self_transfer(row.get("payee_text"), row.get("note")):
        return {"action": "self_transfer"}
    # resolved payee id is the strongest signal
    pid = row.get("payee_id")
    if pid:
        hit = index["by_payee_id"].get(str(pid))
        if hit and hit["share"] >= min_share and hit["count"] >= min_count:
            return {"action": "history", "category_key": hit["category_key"],
                    "source": "payee_id", "confidence": hit["share"]}
    memo = norm_memo(row.get("payee_text"))
    if memo:
        hit = index["by_memo"].get(memo)
        if hit and hit["share"] >= min_share and hit["count"] >= min_count:
            return {"action": "history", "category_key": hit["category_key"],
                    "source": "memo", "confidence": hit["share"]}
    return {"action": "none"}


def plan(uncategorized: list[dict], index: dict[str, dict[str, Any]],
         **kw) -> dict[str, Any]:
    """Classify every uncategorized row and summarize (dry-run report)."""
    decisions = [(r, classify(r, index, **kw)) for r in uncategorized]
    counts = Counter(d["action"] for _, d in decisions)
    return {
        "total": len(uncategorized),
        "history": counts.get("history", 0),
        "self_transfer": counts.get("self_transfer", 0),
        "none": counts.get("none", 0),
        "decisions": decisions,
    }


# tag stamped on rows this job categorizes, so they're filterable + reversible
HISTORY_TAG = "auto:history"


async def run_categorize(pool, *, dry_run: bool = True, min_share: float = 0.6) -> dict[str, Any]:
    """Phase 1: learn from categorized history, apply high-confidence category
    matches to the backlog, and detect self-transfers. Dry-run reports what it
    WOULD do (by category, with samples) and writes nothing."""
    async with pool.acquire() as conn:
        hist = await conn.fetch(
            "SELECT category_key, payee_id::text AS payee_id, payee_text "
            "FROM memory.fin_transaction WHERE deleted_at IS NULL AND category_key IS NOT NULL")
        uncat = await conn.fetch(
            "SELECT id::text AS id, payee_id::text AS payee_id, payee_text, note "
            "FROM memory.fin_transaction WHERE deleted_at IS NULL AND category_key IS NULL")
        cats = await conn.fetch("SELECT key, label FROM memory.fin_category")
    label = {c["key"]: c["label"] for c in cats}

    index = build_history_index([dict(r) for r in hist])
    p = plan([dict(r) for r in uncat], index, min_share=min_share)

    by_cat: Counter = Counter()
    none_memos: Counter = Counter()
    xfer_samples: list[str] = []
    applied = 0
    for r, d in p["decisions"]:
        if d["action"] == "history":
            by_cat[label.get(d["category_key"], d["category_key"])] += 1
        elif d["action"] == "self_transfer":
            if len(xfer_samples) < 12 and r.get("payee_text"):
                xfer_samples.append(r["payee_text"][:48])
        else:
            none_memos[(r.get("payee_text") or "(no memo)")[:48]] += 1

    if not dry_run:
        async with pool.acquire() as conn:
            async with conn.transaction():
                for r, d in p["decisions"]:
                    if d["action"] != "history":
                        continue
                    await conn.execute(
                        """
                        UPDATE memory.fin_transaction
                           SET category_key = $2,
                               tags = ARRAY(SELECT DISTINCT unnest(tags || $3::text[]))
                         WHERE id = $1::uuid AND deleted_at IS NULL AND category_key IS NULL
                        """, r["id"], d["category_key"], [HISTORY_TAG])
                    applied += 1

    return {
        "dry_run": dry_run,
        "uncategorized": p["total"],
        "would_categorize" if dry_run else "categorized": p["history"] if dry_run else applied,
        "self_transfers": p["self_transfer"],
        "left_for_llm": p["none"],
        "by_category": [{"label": k, "n": n} for k, n in by_cat.most_common()],
        "self_transfer_samples": xfer_samples,
        "top_unmatched_memos": [{"memo": m, "n": n} for m, n in none_memos.most_common(20)],
    }


# tag stamped on rows the local LLM categorizes — lower-confidence, for review
LLM_TAG = "auto:llm"

_LLM_SYSTEM = (
    "You label ONE bank/crypto transaction from its memo. Reply with STRICT JSON: "
    '{"category":"<exact label from the list, or empty>","payee":"<clean person or '
    'merchant name, or empty>"}. A person\'s name in any language is a valid payee. '
    "For a generic bank op or a wallet address, use empty for both. Only use a "
    "category label that appears in the list; if unsure, leave category empty."
)


async def _ollama_label(hc, cfg, memo: str, cat_str: str) -> tuple[str, str]:
    r = await hc.post(f"{cfg.ollama_url.rstrip('/')}/api/chat", json={
        "model": cfg.ollama_model, "stream": False, "format": "json",
        "options": {"temperature": 0, "num_predict": 80},
        "messages": [{"role": "system", "content": _LLM_SYSTEM},
                     {"role": "user", "content": f"Categories: {cat_str}\n\nMemo: {memo}"}]})
    r.raise_for_status()
    v = json.loads(r.json().get("message", {}).get("content", "") or "{}")
    return (v.get("category") or "").strip(), (v.get("payee") or "").strip()


async def _resolve_payee(conn, name: str) -> str | None:
    pid = await conn.fetchval(
        "SELECT id::text FROM memory.fin_payee WHERE name ILIKE $1 OR similarity(name,$1) > 0.5 "
        "ORDER BY (name ILIKE $1) DESC, similarity(name,$1) DESC LIMIT 1", name)
    if pid:
        return pid
    row = await conn.fetchrow(
        "INSERT INTO memory.fin_payee (name, source_kind) VALUES ($1,'auto') RETURNING id::text", name)
    return row["id"]


def looks_like_name(memo: str | None) -> bool:
    """A memo that is itself a clean person/entity name (so the payee IS the memo,
    no model needed): short, no digits, few words. Catches P2P transfers like
    'Aleksandra R.', 'Acme Trading Group', 'Ч. Светлана Александровна'."""
    if not memo:
        return False
    m = memo.strip()
    return 0 < len(m) <= 28 and not re.search(r"\d", m) and len(m.split()) <= 3


async def enrich_backlog_cloud(pool, client, model, *, apply: bool = False,
                               limit: int = 400) -> dict[str, Any]:
    """Enrich the backlog with the strong (cloud Haiku) extractor — the local 3b
    model was too weak (defaulted to 'Miscellaneous', missed names). One batched
    tool-call maps each distinct enrichable memo → {category_key, clean payee}.
    A deterministic fallback sets the payee to the memo itself when it's plainly
    a name the model left blank. Applied rows are tagged 'auto:cat' for review."""
    from .finance_import import enrich_statement_rows
    async with pool.acquire() as conn:
        cats = await conn.fetch("SELECT key, label, kind FROM memory.fin_category")
        rows = await conn.fetch(
            "SELECT id::text AS id, payee_text, note FROM memory.fin_transaction "
            "WHERE deleted_at IS NULL AND category_key IS NULL")
    cat_dicts = [dict(c) for c in cats]
    valid = {c["key"] for c in cats}
    key_label = {c["key"]: c["label"] for c in cats}

    groups: dict[str, dict] = {}
    for r in rows:
        memo = r["payee_text"]
        if is_self_transfer(memo, r["note"]) or not is_enrichable(memo):
            continue
        g = groups.setdefault(norm_memo(memo), {"memo": memo.strip(), "ids": []})
        g["ids"].append(r["id"])
    distinct = list(groups.values())[:limit]
    memos = [g["memo"] for g in distinct]

    enrich = await enrich_statement_rows(client, model, memos, cat_dicts) if (client and memos) else {}
    results = []
    for g in distinct:
        m = enrich.get(g["memo"], {})
        cat_key = m.get("category_key") if m.get("category_key") in valid else None
        payee = (m.get("payee") or "").strip()
        if not payee and looks_like_name(g["memo"]):
            payee = g["memo"]
        results.append({"memo": g["memo"], "ids": g["ids"], "cat_key": cat_key, "payee": payee})

    applied = 0
    if apply:
        async with pool.acquire() as conn:
            async with conn.transaction():
                for res in results:
                    pid = await _resolve_payee(conn, res["payee"]) if res["payee"] else None
                    if not res["cat_key"] and not pid:
                        continue
                    for tid in res["ids"]:
                        await conn.execute(
                            """
                            UPDATE memory.fin_transaction
                               SET category_key = COALESCE($2, category_key),
                                   payee_id     = COALESCE($3::uuid, payee_id),
                                   tags = ARRAY(SELECT DISTINCT unnest(tags || $4::text[]))
                             WHERE id = $1::uuid AND deleted_at IS NULL AND category_key IS NULL
                            """, tid, res["cat_key"], pid, ["auto:cat"])
                        applied += 1

    return {
        "apply": apply,
        "distinct_memos": len(distinct),
        "with_category": sum(1 for r in results if r["cat_key"]),
        "with_payee": sum(1 for r in results if r["payee"]),
        "applied_rows": applied,
        "samples": [{"memo": r["memo"][:44], "category": key_label.get(r["cat_key"], ""),
                     "payee": r["payee"], "rows": len(r["ids"])} for r in results[:30]],
    }


async def enrich_backlog_with_ollama(pool, cfg, *, apply: bool = False, limit: int = 400) -> dict[str, Any]:
    """Phase 3: send each DISTINCT enrichable memo (not self / generic / crypto)
    to the local LLM for {category, clean payee}, then apply to every row sharing
    that memo — category_key + a resolved payee_id, tagged 'auto:llm' for review.
    Deduping to distinct memos keeps it to ~one model call per merchant/person."""
    import httpx
    async with pool.acquire() as conn:
        cats = await conn.fetch("SELECT key, label FROM memory.fin_category")
        rows = await conn.fetch(
            "SELECT id::text AS id, payee_text, note FROM memory.fin_transaction "
            "WHERE deleted_at IS NULL AND category_key IS NULL")
    label_to_key = {(c["label"] or "").strip().lower(): c["key"] for c in cats}
    cat_str = ", ".join(sorted({(c["label"] or "").strip() for c in cats if c["label"]}))

    groups: dict[str, dict] = {}
    for r in rows:
        memo = r["payee_text"]
        if is_self_transfer(memo, r["note"]) or not is_enrichable(memo):
            continue
        g = groups.setdefault(norm_memo(memo), {"memo": memo.strip(), "ids": []})
        g["ids"].append(r["id"])
    distinct = list(groups.values())[:limit]

    results = []
    async with httpx.AsyncClient(timeout=90) as hc:
        for g in distinct:
            try:
                cat_label, payee = await _ollama_label(hc, cfg, g["memo"], cat_str)
            except Exception as e:  # noqa: BLE001 — a miss must not break the batch
                log.warning("ollama enrich failed %r: %s", g["memo"][:40], e)
                continue
            results.append({"memo": g["memo"], "ids": g["ids"], "payee": payee,
                            "cat_key": label_to_key.get(cat_label.strip().lower()),
                            "cat_label": cat_label})

    applied = 0
    if apply:
        async with pool.acquire() as conn:
            async with conn.transaction():
                for res in results:
                    pid = await _resolve_payee(conn, res["payee"]) if res["payee"] else None
                    if not res["cat_key"] and not pid:
                        continue
                    for tid in res["ids"]:
                        await conn.execute(
                            """
                            UPDATE memory.fin_transaction
                               SET category_key = COALESCE($2, category_key),
                                   payee_id     = COALESCE($3::uuid, payee_id),
                                   tags = ARRAY(SELECT DISTINCT unnest(tags || $4::text[]))
                             WHERE id = $1::uuid AND deleted_at IS NULL AND category_key IS NULL
                            """, tid, res["cat_key"], pid, [LLM_TAG])
                        applied += 1

    return {
        "apply": apply,
        "distinct_memos": len(distinct),
        "with_category": sum(1 for r in results if r["cat_key"]),
        "with_payee": sum(1 for r in results if r["payee"]),
        "applied_rows": applied,
        "samples": [{"memo": r["memo"][:44], "category": r["cat_label"] if r["cat_key"] else "",
                     "payee": r["payee"], "rows": len(r["ids"])} for r in results[:30]],
    }
