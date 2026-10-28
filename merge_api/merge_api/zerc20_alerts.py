"""Email alerts for notable zERC20 activity.

Two rules, both checked against the locally indexed data (no Dune, no RPC):

1. **Large transaction** — a single wrap, redeem or unwrap worth more than
   `ZERC20_ALERT_USD` (default $20,000).
2. **Burst** — more than `ZERC20_ALERT_BURST` operations (default 100) inside a
   rolling `ZERC20_ALERT_WINDOW_MIN` (default 60) minutes, across all chains.

Calibration when this was written (2026-09-25), against everything indexed
since February: rule 1 would have fired 13 times, roughly once or twice a
month, and every hit was organic rather than bot traffic. Rule 2 would never
have fired — the busiest hour on record is 67 operations — so it currently acts
as a tripwire for growth rather than a routine notification.

Every alert is recorded in zerc20.alert_sent and never re-sent: a large
transaction is keyed by its log, a burst by its clock hour.
"""
from __future__ import annotations

import json
import logging
import os

from . import gmail_send

log = logging.getLogger(__name__)

USD_THRESHOLD = float(os.environ.get("ZERC20_ALERT_USD", "20000"))
BURST_COUNT = int(os.environ.get("ZERC20_ALERT_BURST", "100"))
WINDOW_MIN = int(os.environ.get("ZERC20_ALERT_WINDOW_MIN", "60"))
# Only look this far back, so a backfill or a restore cannot email a year of
# history in one go.
LOOKBACK_HOURS = int(os.environ.get("ZERC20_ALERT_LOOKBACK_HOURS", "48"))

DASHBOARD = "https://zerc20-dashboard.vercel.app"
EXPLORER = {
    "Ethereum": "https://etherscan.io/tx/",
    "Arbitrum": "https://arbiscan.io/tx/",
    "Base": "https://basescan.org/tx/",
    "BNB Chain": "https://bscscan.com/tx/",
    "Polygon": "https://polygonscan.com/tx/",
    "Kaia": "https://kaiascan.io/tx/",
}


def _recipients() -> list[str]:
    raw = os.environ.get("ZERC20_ALERT_TO", "")
    return [a.strip() for a in raw.split(",") if a.strip()]


def _usd(v: float) -> str:
    return f"${v:,.0f}" if v >= 1 else f"${v:,.2f}"


async def _large_transactions(conn, threshold: float) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT o.chain, o.token, o.tx_type, o.wallet, o.tx_hash, o.log_index,
               o.amount, o.usd_value, o.is_organic, o.block_time
        FROM zerc20.v_operation o
        WHERE o.usd_value > $1
          AND o.block_time > now() - ($2 || ' hours')::interval
          AND NOT EXISTS (
                SELECT 1 FROM zerc20.alert_sent a
                WHERE a.kind = 'large_tx'
                  AND a.key = o.chain || ':' || o.tx_hash || ':' || o.log_index)
        ORDER BY o.usd_value DESC
        """, threshold, str(LOOKBACK_HOURS))
    return [dict(r) for r in rows]


async def _burst(conn, count: int) -> dict | None:
    row = await conn.fetchrow(
        """
        SELECT count(*) AS n,
               min(block_time) AS started,
               max(block_time) AS latest,
               count(*) FILTER (WHERE is_organic) AS organic,
               to_char(date_trunc('hour', now()), 'YYYY-MM-DD HH24') AS bucket
        FROM zerc20.v_operation
        WHERE block_time > now() - ($1 || ' minutes')::interval
        """, str(WINDOW_MIN))
    if not row or row["n"] < count:
        return None
    already = await conn.fetchval(
        "SELECT 1 FROM zerc20.alert_sent WHERE kind = 'burst' AND key = $1", row["bucket"])
    return None if already else dict(row)


def _compose(large: list[dict], burst: dict | None, threshold: float) -> tuple[str, str]:
    lines, html = [], []
    if burst:
        lines.append(f"BURST: {burst['n']} operations in the last {WINDOW_MIN} minutes "
                     f"({burst['organic']} from non-bot wallets).")
        lines.append(f"  first {burst['started']:%Y-%m-%d %H:%M} UTC · latest {burst['latest']:%H:%M} UTC")
        lines.append("")
        html.append(f"<p><strong>Burst:</strong> {burst['n']} operations in the last "
                    f"{WINDOW_MIN} minutes ({burst['organic']} from non-bot wallets).</p>")
    if large:
        lines.append(f"LARGE TRANSACTION{'S' if len(large) > 1 else ''} over {_usd(threshold)}:")
        html.append(f"<p><strong>Large transaction{'s' if len(large) > 1 else ''}</strong> "
                    f"over {_usd(threshold)}:</p><ul>")
        for r in large:
            who = "user wallet" if r["is_organic"] else "bot/system wallet"
            link = EXPLORER.get(r["chain"], "") + r["tx_hash"]
            lines.append(f"  {_usd(r['usd_value'])}  {r['tx_type']} {r['token']} on {r['chain']}"
                         f"  ({who})  {r['block_time']:%Y-%m-%d %H:%M} UTC")
            lines.append(f"    {link}")
            html.append(f"<li><strong>{_usd(r['usd_value'])}</strong> — {r['tx_type']} "
                        f"{r['token']} on {r['chain']} ({who}, "
                        f"{r['block_time']:%Y-%m-%d %H:%M} UTC)<br>"
                        f'<a href="{link}">{r["tx_hash"][:18]}…</a></li>')
        html.append("</ul>")
    lines += ["", f"Dashboard: {DASHBOARD}",
              "", "Sent by the zERC20 indexer. Amounts use the dashboard's fixed "
              "reference prices, so they are approximate."]
    html.append(f'<p><a href="{DASHBOARD}">Open the dashboard</a></p>'
                "<p style='color:#888;font-size:12px'>Sent by the zERC20 indexer. "
                "Amounts use the dashboard's fixed reference prices, so they are approximate.</p>")
    return "\n".join(lines), "".join(html)


async def check_and_alert(pool, cfg, *, dry_run: bool = False,
                          usd_threshold: float | None = None,
                          burst_count: int | None = None,
                          to_override: list[str] | None = None) -> dict:
    """Find anything notable since the last pass and email it once.

    The thresholds default to the configured ones; overrides exist so a
    dry run can exercise the real query and formatting on quiet data."""
    threshold = USD_THRESHOLD if usd_threshold is None else usd_threshold
    burst_at = BURST_COUNT if burst_count is None else burst_count
    to = to_override or _recipients()
    async with pool.acquire() as conn:
        large = await _large_transactions(conn, threshold)
        burst = await _burst(conn, burst_at)

        if not large and not burst:
            return {"alerted": False, "large_tx": 0, "burst": False,
                    "recipients": len(to), "reason": "nothing above thresholds"}

        subject_bits = []
        if burst:
            subject_bits.append(f"{burst['n']} txs in {WINDOW_MIN}m")
        if large:
            biggest = max(r["usd_value"] for r in large)
            subject_bits.append(f"{_usd(biggest)} {large[0]['tx_type'].lower()}"
                                if len(large) == 1 else f"{len(large)} large txs, top {_usd(biggest)}")
        subject = "zERC20 alert — " + " · ".join(subject_bits)
        body, html = _compose(large, burst, threshold)

        if dry_run:
            return {"alerted": False, "dry_run": True, "subject": subject, "body": body,
                    "large_tx": len(large), "burst": bool(burst), "recipients": to}
        if not to:
            log.warning("zERC20 alert triggered but ZERC20_ALERT_TO is empty")
            return {"alerted": False, "large_tx": len(large), "burst": bool(burst),
                    "reason": "no recipients configured"}
        if not cfg.email_send_account:
            log.warning("zERC20 alert triggered but BOT_EMAIL_SEND_ACCOUNT is unset")
            return {"alerted": False, "large_tx": len(large), "burst": bool(burst),
                    "reason": "no sending account configured"}

        sent, failed = [], []
        for address in to:
            try:
                await gmail_send.send_email(
                    pool, client_secrets_path=cfg.gcal_client_secrets,
                    account_email=cfg.email_send_account, to_email=address,
                    subject=subject, body=body, html=html)
                sent.append(address)
            except Exception as e:  # noqa: BLE001 — one bad address must not lose the rest
                log.exception("zERC20 alert to %s failed", address)
                failed.append({"to": address, "error": str(e)[:200]})

        # Record only on a real send, so a total failure retries next pass.
        if sent:
            for r in large:
                await conn.execute(
                    "INSERT INTO zerc20.alert_sent (kind, key, detail) VALUES ('large_tx', $1, $2) "
                    "ON CONFLICT DO NOTHING",
                    f"{r['chain']}:{r['tx_hash']}:{r['log_index']}",
                    json.dumps({"usd": float(r["usd_value"]), "chain": r["chain"],
                                "token": r["token"], "type": r["tx_type"],
                                "organic": r["is_organic"]}))
            if burst:
                await conn.execute(
                    "INSERT INTO zerc20.alert_sent (kind, key, detail) VALUES ('burst', $1, $2) "
                    "ON CONFLICT DO NOTHING",
                    burst["bucket"], json.dumps({"count": burst["n"], "organic": burst["organic"],
                                                 "window_minutes": WINDOW_MIN}))
        return {"alerted": bool(sent), "subject": subject, "large_tx": len(large),
                "burst": bool(burst), "sent_to": sent, "failed": failed}
