"""One sync pass across all active accounts.

Strategy: incremental walk by date.
  - Find the latest internal_date we already have for this account.
  - Query the Gmail API with q='after:YYYY/MM/DD' (date-only granularity).
  - Page through results, fetch each message in full, insert.

Why not History API: history events expire after ~7 days, and a stale
history_id forces a re-sync anyway. Date-based is simpler, idempotent
(ON CONFLICT on (account_email, message_id)), and works for both initial
backfill and steady-state."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from . import client as gclient, db
from .config import Config

log = logging.getLogger(__name__)


def _list_message_ids(service, query: str, max_results: int) -> list[str]:
    """Page through messages.list until cap reached or no more pages."""
    ids: list[str] = []
    page_token = None
    while True:
        resp = service.users().messages().list(
            userId="me",
            q=query,
            maxResults=min(500, max_results - len(ids)),
            pageToken=page_token,
        ).execute()
        for m in resp.get("messages") or []:
            ids.append(m["id"])
            if len(ids) >= max_results:
                return ids
        page_token = resp.get("nextPageToken")
        if not page_token:
            return ids


def _get_message(service, msg_id: str) -> dict:
    return service.users().messages().get(userId="me", id=msg_id, format="full").execute()


# --- read-state sync (round 2 / P4): read elsewhere ⇒ read here ---------------
# Gmail's UNREAD label drifts from our stored copy the moment mail is read in
# another client. Rather than the History API (events expire ~7 days — same
# reason the message sync is date-based), each run reconciles by diffing Gmail's
# current unread ids in a window (a cheap id-only messages.list) against ours.

READ_SYNC_WINDOW_DAYS = 45
READ_SYNC_CAP = 10_000   # id-only list; safety valve, not a quota concern


def read_state_diff(db_unread: set[str], gmail_unread: set[str],
                    *, gmail_truncated: bool) -> tuple[list[str], list[str]]:
    """(mark_read, mark_unread) message-id lists from the two unread sets.
    When Gmail's list hit the cap it is INCOMPLETE — clearing UNREAD based on
    absence from a truncated list would wrongly mark mail read, so mark_read is
    suppressed; mark_unread (based on presence) stays safe either way."""
    mark_read = [] if gmail_truncated else sorted(db_unread - gmail_unread)
    mark_unread = sorted(gmail_unread - db_unread)
    return mark_read, mark_unread


async def _sync_read_state(pool, service, account_email: str) -> dict:
    """Reconcile the UNREAD label with Gmail inside the window. Best-effort:
    a failure here never fails the message sync."""
    gmail_ids = await asyncio.to_thread(
        _list_message_ids, service,
        f"is:unread newer_than:{READ_SYNC_WINDOW_DAYS}d", READ_SYNC_CAP)
    truncated = len(gmail_ids) >= READ_SYNC_CAP
    db_ids = await db.unread_message_ids(pool, account_email, days=READ_SYNC_WINDOW_DAYS)
    mark_read, mark_unread = read_state_diff(set(db_ids), set(gmail_ids),
                                             gmail_truncated=truncated)
    n_read = await db.mark_messages_read(pool, account_email, mark_read)
    n_unread = await db.mark_messages_unread(pool, account_email, mark_unread)
    return {"read_synced": n_read, "unread_restored": n_unread,
            "truncated": truncated}


async def _sync_one(pool, cfg: Config, account_email: str, refresh_token: str,
                    *, mode: str = "forward") -> dict:
    """Per-account sync. mode='forward' (default) fetches messages newer
    than the latest we already have — for steady-state incremental sync.
    mode='backfill' fetches messages older than the earliest we have,
    walking history backwards — for filling out old years on first
    bring-up.

    Returns counts dict for logging at the call site."""
    service = gclient.build_service(cfg.client_secrets_path, refresh_token, list(cfg.scopes))

    if mode == "backfill":
        first_date = await db.earliest_message_date(pool, account_email)
        if first_date:
            query = f"before:{first_date}"
        else:
            # No messages yet — same as forward initial backfill.
            cutoff = datetime.now(timezone.utc) - timedelta(days=cfg.sync_initial_days)
            query = f"after:{cutoff.strftime('%Y/%m/%d')}"
    else:
        last_date = await db.latest_message_date(pool, account_email)
        if last_date:
            # Re-query the day we last saw so a partial day completes; the unique
            # constraint absorbs the rows we already had.
            query = f"after:{last_date}"
        else:
            cutoff = datetime.now(timezone.utc) - timedelta(days=cfg.sync_initial_days)
            query = f"after:{cutoff.strftime('%Y/%m/%d')}"

    log.info("sync %s (%s): query=%s cap=%d",
             account_email, mode, query, cfg.sync_messages_per_account)

    try:
        ids = await asyncio.to_thread(
            _list_message_ids, service, query, cfg.sync_messages_per_account
        )
    except Exception as e:
        # Revoked/expired token or scope-insufficient → flag for reconnect and
        # return cleanly (the account drops out of the active set next run).
        if gclient.is_reauth_error(e):
            await db.mark_reauth_needed(pool, account_email, repr(e))
            return {"inserted": 0, "skipped": 0, "failed": 0,
                    "fetched": 0, "reauth": 1}
        raise
    log.info("sync %s: %d message ids to fetch", account_email, len(ids))

    inserted = skipped = failed = 0
    last_history_id: str | None = None
    for i, mid in enumerate(ids):
        try:
            msg = await asyncio.to_thread(_get_message, service, mid)
        except Exception:
            log.exception("sync %s: get(%s) failed", account_email, mid)
            failed += 1
            continue
        last_history_id = msg.get("historyId") or last_history_id
        row = gclient.api_message_to_row(account_email=account_email, msg=msg)
        if row is None:
            skipped += 1
            continue
        was_new = await db.insert_message(pool, row)
        if was_new:
            inserted += 1
        else:
            skipped += 1
        if (i + 1) % 100 == 0:
            log.info("sync %s: progress fetched=%d inserted=%d skipped=%d failed=%d",
                     account_email, i + 1, inserted, skipped, failed)

    stats = {"inserted": inserted, "skipped": skipped, "failed": failed,
             "fetched": len(ids)}

    # Read-state reconcile (steady-state runs only — pointless mid-backfill).
    if mode == "forward":
        try:
            stats.update(await _sync_read_state(pool, service, account_email))
        except Exception:  # noqa: BLE001
            log.exception("sync %s: read-state reconcile failed (non-fatal)", account_email)

    await db.update_account_sync(pool, account_email, last_history_id)
    return stats


async def run(cfg: Config, *, mode: str = "forward") -> int:
    pool = await db.connect(cfg.db_url)
    try:
        accounts = await db.list_active_accounts(pool)
        log.info("gmail sync: %d active accounts (mode=%s)", len(accounts), mode)
        any_failed = False
        for acc in accounts:
            try:
                stats = await _sync_one(
                    pool, cfg, acc["email"], acc["refresh_token"], mode=mode,
                )
                log.info("sync %s done: %s", acc["email"], stats)
                if stats["failed"]:
                    any_failed = True
            except Exception:
                log.exception("sync %s: top-level failure", acc["email"])
                any_failed = True
        return 1 if any_failed else 0
    finally:
        await pool.close()
