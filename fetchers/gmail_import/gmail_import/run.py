"""Streaming mbox importer. Walks the mbox one message at a time, batches
N rows into raw.gmail_message, logs progress every K messages.

Memory profile: bounded by batch_size × average message size. For very
large bodies (40MB+ HTML newsletters) the peak is one message, not the
whole mbox."""

from __future__ import annotations

import logging
import mailbox
from typing import Any

from . import db, parse
from .config import Config

log = logging.getLogger(__name__)


async def run(cfg: Config) -> int:
    log.info(
        "gmail import: mbox=%s account=%s skip_spam=%s skip_trash=%s dry_run=%s",
        cfg.mbox_path, cfg.account_email,
        cfg.skip_spam, cfg.skip_trash, cfg.dry_run,
    )

    pool = None
    if not cfg.dry_run:
        pool = await db.connect(cfg.db_url)
        await db.ensure_account(pool, cfg.account_email)

    seen = 0
    parsed = 0
    skipped_spam = 0
    skipped_trash = 0
    parse_failed = 0
    inserted = 0
    batch: list[dict[str, Any]] = []

    try:
        # mailbox.mbox is lazy; opening the file does not read it into memory.
        mbox = mailbox.mbox(cfg.mbox_path)
        for raw_msg in mbox:
            seen += 1
            if cfg.limit and seen > cfg.limit:
                break

            labels = parse.parse_labels(raw_msg)
            label_set = {lab.lower() for lab in labels}
            if cfg.skip_spam and "spam" in label_set:
                skipped_spam += 1
                continue
            if cfg.skip_trash and "trash" in label_set:
                skipped_trash += 1
                continue

            row = parse.message_to_row(raw_msg, account_email=cfg.account_email)
            if row is None:
                parse_failed += 1
                continue
            parsed += 1

            if cfg.dry_run:
                if parsed <= 3:
                    log.info(
                        "DRY: %s | %s -> %s | %s",
                        row["internal_date"].isoformat()[:19],
                        row["from_address"], (row["to_addresses"] or [None])[0],
                        (row["subject"] or "")[:80],
                    )
            else:
                batch.append(row)
                if len(batch) >= cfg.batch_size:
                    inserted += await db.insert_batch(pool, batch)
                    batch.clear()

            if seen % cfg.progress_every == 0:
                log.info(
                    "progress: seen=%d parsed=%d inserted=%d "
                    "skipped(spam=%d trash=%d) parse_failed=%d",
                    seen, parsed, inserted,
                    skipped_spam, skipped_trash, parse_failed,
                )

        if batch and not cfg.dry_run:
            inserted += await db.insert_batch(pool, batch)
    finally:
        if pool is not None:
            await pool.close()

    log.info(
        "import done: seen=%d parsed=%d inserted=%d "
        "skipped(spam=%d trash=%d) parse_failed=%d",
        seen, parsed, inserted, skipped_spam, skipped_trash, parse_failed,
    )
    return 0
