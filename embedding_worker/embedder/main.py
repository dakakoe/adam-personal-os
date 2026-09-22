from __future__ import annotations

import asyncio
import logging

import asyncpg
import pgvector.asyncpg

from . import embed
from .config import Config

log = logging.getLogger(__name__)


async def _register_vector(conn: asyncpg.Connection) -> None:
    """asyncpg needs to learn the pgvector type once per connection."""
    await pgvector.asyncpg.register_vector(conn)


async def _connect(db_url: str) -> asyncpg.Pool:
    return await asyncpg.create_pool(
        db_url,
        min_size=1,
        max_size=3,
        statement_cache_size=0,
        init=_register_vector,
    )


async def _fetch_pending(
    pool: asyncpg.Pool, *, after_id: str | None, limit: int
) -> list[asyncpg.Record]:
    """Interactions that have a body and don't yet have a row in
    memory.interaction_embedding. Cursor-paginated by interaction id so
    a broken row never traps us. Skips merged-away persons' rows."""
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT i.id::text AS interaction_id, i.body
            FROM canonical.interaction i
            LEFT JOIN memory.interaction_embedding e
              ON e.interaction_id = i.id
            WHERE e.interaction_id IS NULL
              AND i.body IS NOT NULL
              AND length(i.body) > 0
              AND ($1::text IS NULL OR i.id::text > $1)
            ORDER BY i.id
            LIMIT $2
            """,
            after_id,
            limit,
        )


async def _fetch_pending_mail(
    pool: asyncpg.Pool, *, after_id: int | None, limit: int
) -> list[asyncpg.Record]:
    """Gmail messages with any text and no row in memory.mail_embedding.
    Cursor-paginated by the BIGSERIAL id (a broken row never traps us)."""
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT m.id AS gmail_id, m.from_address, m.subject, m.body_text
            FROM raw.gmail_message m
            LEFT JOIN memory.mail_embedding e
              ON e.gmail_message_id = m.id
            WHERE e.gmail_message_id IS NULL
              AND (COALESCE(m.subject, '') <> '' OR COALESCE(m.body_text, '') <> '')
              AND ($1::bigint IS NULL OR m.id > $1)
            ORDER BY m.id
            LIMIT $2
            """,
            after_id,
            limit,
        )


def _mail_text(from_address: str | None, subject: str | None,
               body: str | None, max_chars: int) -> str:
    """The text a mail message is embedded as — the mail_ml build_text shape
    (sender-led, subject doubled for weight, body capped). Whitespace is
    collapsed; the 'passage: ' prefix is added inside embed.encode, NOT here."""
    import re
    sender = re.sub(r"\s+", " ", (from_address or "")).strip().lower()
    subj = re.sub(r"\s+", " ", (subject or "")).strip()
    bod = re.sub(r"\s+", " ", (body or "")).strip()[:max_chars]
    return f"From: {sender}\n{subj}\n{subj}\n{bod}".strip()


async def _write_mail_batch(
    pool: asyncpg.Pool,
    rows: list[asyncpg.Record],
    vectors: list[list[float]],
    model_name: str,
) -> None:
    records = [(r["gmail_id"], v, model_name) for r, v in zip(rows, vectors)]
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO memory.mail_embedding
              (gmail_message_id, embedding, embedding_model)
            VALUES ($1, $2, $3)
            ON CONFLICT (gmail_message_id) DO NOTHING
            """,
            records,
        )


async def _write_batch(
    pool: asyncpg.Pool,
    rows: list[asyncpg.Record],
    vectors: list[list[float]],
    model_name: str,
) -> None:
    """Insert (interaction_id, embedding, embedding_model) tuples in one
    executemany. ON CONFLICT DO NOTHING covers the rare case where two
    workers race; with our single-process setup this is just defensive."""
    records = [
        (r["interaction_id"], v, model_name)
        for r, v in zip(rows, vectors)
    ]
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO memory.interaction_embedding
              (interaction_id, embedding, embedding_model)
            VALUES ($1::uuid, $2, $3)
            ON CONFLICT (interaction_id) DO NOTHING
            """,
            records,
        )


async def run(cfg: Config) -> None:
    pool = await _connect(cfg.db_url)
    model = embed.load_model(cfg)
    log.info(
        "embedder ready: batch=%d max_chars=%d poll=%ds",
        cfg.batch_size, cfg.max_chars, cfg.poll_interval_sec,
    )

    after_id: str | None = None
    mail_after_id: int | None = None
    embedded = 0
    failed = 0
    mail_embedded = 0
    mail_failed = 0

    try:
        while True:
            batch = await _fetch_pending(
                pool, after_id=after_id, limit=cfg.batch_size,
            )
            if not batch:
                if after_id is not None:
                    log.info("queue drained this pass: embedded=%d failed=%d",
                             embedded, failed)
                after_id = None

                # Interactions stay real-time; mail drains in the idle gaps
                # (Ollama expansion — feeds memory.mail_embedding for
                # local_ask / semantic_search_mail).
                if cfg.mail_enabled:
                    mail_batch = await _fetch_pending_mail(
                        pool, after_id=mail_after_id, limit=cfg.batch_size,
                    )
                    if mail_batch:
                        texts = [
                            _mail_text(r["from_address"], r["subject"],
                                       r["body_text"], cfg.max_chars)
                            for r in mail_batch
                        ]
                        try:
                            vectors = await embed.encode(model, texts)
                            await _write_mail_batch(pool, mail_batch, vectors, cfg.model_name)
                            mail_embedded += len(mail_batch)
                        except Exception:
                            log.exception("mail batch encode/write failed (%d rows)",
                                          len(mail_batch))
                            mail_failed += len(mail_batch)
                        finally:
                            mail_after_id = mail_batch[-1]["gmail_id"]
                        if mail_embedded % (cfg.batch_size * 10) == 0:
                            log.info("mail progress: embedded=%d failed=%d cursor=%s",
                                     mail_embedded, mail_failed, mail_after_id)
                        continue   # keep draining mail until interactions reappear
                    if mail_after_id is not None:
                        log.info("mail queue drained: embedded=%d failed=%d",
                                 mail_embedded, mail_failed)
                        mail_after_id = None

                await asyncio.sleep(cfg.poll_interval_sec)
                continue

            texts = [
                (r["body"] or "")[: cfg.max_chars] for r in batch
            ]
            try:
                vectors = await embed.encode(model, texts)
                await _write_batch(pool, batch, vectors, cfg.model_name)
                embedded += len(batch)
            except Exception:
                log.exception("batch encode/write failed (%d rows)", len(batch))
                failed += len(batch)
            finally:
                after_id = batch[-1]["interaction_id"]

            if embedded % (cfg.batch_size * 10) == 0:
                log.info("progress: embedded=%d failed=%d cursor=%s",
                         embedded, failed, after_id)
    finally:
        await pool.close()
        log.info("shutting down: embedded=%d failed=%d", embedded, failed)
