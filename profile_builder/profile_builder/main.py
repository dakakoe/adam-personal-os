from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import anthropic
import asyncpg
import pgvector.asyncpg

from .config import Config
from . import local_llm, prompt

log = logging.getLogger(__name__)


async def _register_vector(conn: asyncpg.Connection) -> None:
    await pgvector.asyncpg.register_vector(conn)


def _load_embedder(cfg: Config):
    import torch
    from sentence_transformers import SentenceTransformer

    torch.set_num_threads(1)
    log.info("loading embedder %s", cfg.embedding_model)
    return SentenceTransformer(
        cfg.embedding_model,
        device="cpu",
        cache_folder=cfg.embedding_cache_dir,
    )


def _sig_sql(pid: str) -> str:
    """SQL scalar that fingerprints a person's profile INPUTS: interaction count
    + the full identity set (source:id:evidence — LinkedIn position/company live
    in evidence). When this changes, the summary is out of date and must rebuild.
    MUST stay byte-identical to the baseline UPDATE in the input_sig migration."""
    return (
        "md5("
        f"(SELECT count(*)::text FROM canonical.interaction WHERE person_id = {pid})"
        " || '|' || "
        "coalesce((SELECT string_agg(i.source || ':' || i.source_id || ':' || "
        "coalesce(i.evidence::text, ''), '§' ORDER BY i.id) "
        f"FROM canonical.identity i WHERE i.person_id = {pid}), '')"
        ")"
    )


async def _fetch_candidates(
    pool: asyncpg.Pool,
    *,
    limit: int,
    rebuild: bool,
    refresh_older_than_days: int | None,
) -> list[asyncpg.Record]:
    """Active persons sorted by interaction volume (highest user value
    first). Three mutually-exclusive modes selected by flag:
    - refresh_older_than_days set: only existing profiles older than N days
      (the cron-driven drift case). NULL last_built_at also qualifies, to
      heal any orphan rows from pre-`last_built_at` migrations.
    - rebuild=True: every person, overwriting existing profiles.
    - default: only persons without a profile yet (first-build case).

    Joined with telegram identity + bio for the prompt context."""
    if refresh_older_than_days is not None:
        mode_clause = (
            "AND (mp.last_built_at IS NULL "
            "     OR mp.last_built_at < now() - make_interval(days => $2) "
            f"    OR mp.input_sig IS DISTINCT FROM {_sig_sql('p.id')})"
        )
    elif rebuild:
        mode_clause = ""
    else:
        mode_clause = "AND mp.person_id IS NULL"

    async with pool.acquire() as conn:
        sql = f"""
            SELECT p.id::text AS person_id,
                   p.display_name,
                   p.sensitive,
                   tg.source_id AS telegram_id,
                   u.username   AS telegram_username,
                   u.about      AS telegram_bio,
                   (SELECT count(*) FROM canonical.interaction
                      WHERE person_id = p.id) AS message_count
            FROM canonical.person p
            LEFT JOIN LATERAL (
              SELECT i.source_id
              FROM canonical.identity i
              WHERE i.person_id = p.id AND i.source = 'telegram'
              ORDER BY i.id LIMIT 1
            ) tg ON true
            LEFT JOIN raw.telegram_user u
              ON tg.source_id IS NOT NULL
             AND u.source_user_id = tg.source_id::bigint
            LEFT JOIN memory.profile mp ON mp.person_id = p.id
            WHERE p.merged_into IS NULL
              {mode_clause}
              AND (SELECT count(*) FROM canonical.interaction WHERE person_id = p.id) > 0
            -- changed/never-built inputs jump ahead of routine age-drift refresh:
            -- a just-enriched contact should rebuild before we re-touch stale-only ones.
            ORDER BY (mp.input_sig IS NULL) DESC, message_count DESC
            LIMIT $1
            """
        if refresh_older_than_days is not None:
            return await conn.fetch(sql, limit, refresh_older_than_days)
        return await conn.fetch(sql, limit)


async def _fetch_person_context(
    pool: asyncpg.Pool, person_id: str, *, recent_n: int
) -> dict[str, Any]:
    """Stats + recent messages + already-extracted signals + per-source
    bios for the prompt. Bios are pulled from raw.telegram_user (about),
    canonical.identity evidence (linkedin position+company),
    raw.linkedin_imported_contact (title), and raw.google_contact
    (notes, organization+job_title). The LLM sees them as a labeled list
    so it can consolidate provenance-aware into the unified summary."""
    async with pool.acquire() as conn:
        stats = await conn.fetchrow(
            """
            SELECT count(*)::bigint                              AS message_count,
                   count(*) FILTER (WHERE direction='inbound')   AS inbound_count,
                   count(*) FILTER (WHERE direction='outbound')  AS outbound_count,
                   min(occurred_at)                              AS first_message_at,
                   max(occurred_at)                              AS last_message_at,
                   array_agg(DISTINCT channel ORDER BY channel)  AS channels
            FROM canonical.interaction
            WHERE person_id = $1::uuid
            """,
            person_id,
        )
        # Most recent N messages with non-empty body; chronological order in
        # the prompt is more natural so we reverse the desc result.
        rows = await conn.fetch(
            """
            SELECT occurred_at, direction, body
            FROM canonical.interaction
            WHERE person_id = $1::uuid
              AND body IS NOT NULL AND length(body) > 0
            ORDER BY occurred_at DESC
            LIMIT $2
            """,
            person_id, recent_n,
        )
        signals = await conn.fetch(
            """
            SELECT signal_type, value, source
            FROM memory.extracted_signal
            WHERE person_id = $1::uuid
            ORDER BY signal_type, value
            """,
            person_id,
        )
        # Same union as merge_api.queries.PERSON_BIOS_SQL — kept inline here
        # to avoid a cross-app import. If you ever change this, update both.
        bios = await conn.fetch(
            """
            WITH telegram_bio AS (
              SELECT 'telegram'::text AS source, 'bio'::text AS kind, u.about AS text
              FROM canonical.identity i
              JOIN raw.telegram_user u ON u.source_user_id = i.source_id::bigint
              WHERE i.source = 'telegram' AND i.person_id = $1::uuid
                AND u.about IS NOT NULL AND length(u.about) > 0
              LIMIT 1
            ),
            linkedin_role AS (
              SELECT 'linkedin'::text AS source, 'role'::text AS kind,
                     trim(BOTH ' · ' FROM
                          concat_ws(' · ',
                                   NULLIF(i.evidence->>'position', ''),
                                   NULLIF(i.evidence->>'company',  ''))) AS text
              FROM canonical.identity i
              WHERE i.source = 'linkedin' AND i.person_id = $1::uuid
                AND (i.evidence->>'position' IS NOT NULL OR i.evidence->>'company' IS NOT NULL)
              LIMIT 1
            ),
            linkedin_imported AS (
              SELECT DISTINCT ON (g.title)
                     'linkedin'::text AS source, 'title'::text AS kind, g.title AS text
              FROM raw.linkedin_imported_contact g
              JOIN canonical.identity ei
                ON ei.source='email' AND ei.person_id = $1::uuid
               AND lower(ei.source_id) = ANY(SELECT lower(e) FROM unnest(g.emails) e)
              WHERE g.title IS NOT NULL AND length(g.title) > 0
            ),
            google_notes AS (
              SELECT 'google_contacts'::text AS source, 'notes'::text AS kind, g.notes AS text
              FROM raw.google_contact g
              JOIN canonical.identity ei
                ON ei.source='email' AND ei.person_id = $1::uuid
               AND lower(ei.source_id) = ANY(SELECT lower(e) FROM unnest(g.emails) e)
              WHERE g.notes IS NOT NULL AND length(g.notes) > 0
            ),
            google_role AS (
              SELECT 'google_contacts'::text AS source, 'role'::text AS kind,
                     trim(BOTH ' · ' FROM
                          concat_ws(' · ',
                                   NULLIF(g.job_title,    ''),
                                   NULLIF(g.organization, ''))) AS text
              FROM raw.google_contact g
              JOIN canonical.identity ei
                ON ei.source='email' AND ei.person_id = $1::uuid
               AND lower(ei.source_id) = ANY(SELECT lower(e) FROM unnest(g.emails) e)
              WHERE g.job_title IS NOT NULL OR g.organization IS NOT NULL
            )
            SELECT * FROM telegram_bio
            UNION ALL SELECT * FROM linkedin_role
            UNION ALL SELECT * FROM linkedin_imported
            UNION ALL SELECT * FROM google_notes
            UNION ALL SELECT * FROM google_role
            """,
            person_id,
        )
    # Dedupe identical (source, text) tuples — same logic as merge_api list_bios.
    bios_out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for b in bios:
        text = (b["text"] or "").strip()
        if not text:
            continue
        key = (b["source"], text.lower())
        if key in seen:
            continue
        seen.add(key)
        bios_out.append({"source": b["source"], "kind": b["kind"], "text": text})
    return {
        "stats": stats,
        "messages": list(reversed([dict(r) for r in rows])),
        "signals": [dict(r) for r in signals],
        "bios": bios_out,
    }


async def _generate(
    client: anthropic.AsyncAnthropic, cfg: Config, payload: str
) -> dict[str, Any] | None:
    """One LLM call using forced tool-use. Returns the parsed tool input
    (with `summary` and `structured` keys) or None if the model didn't call
    the tool. Raises on transport/API errors so the caller can log and skip
    a single bad row without trapping the whole run."""
    resp = await client.messages.create(
        model=cfg.model,
        max_tokens=1200,
        system=prompt.SYSTEM_PROMPT,
        tools=[prompt.SAVE_PROFILE_TOOL],
        tool_choice={"type": "tool", "name": "save_profile"},
        messages=[{"role": "user", "content": payload}],
    )
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "save_profile":
            return dict(block.input)
    return None


def _embed(embedder, summary: str) -> list[float]:
    """Same passage: prefix the embedder worker uses, so profile and
    interaction embeddings share a vector space."""
    arr = embedder.encode(
        [f"passage: {summary}"],
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    return arr[0].tolist()


async def run(cfg: Config) -> int:
    pool = await asyncpg.create_pool(
        cfg.db_url, min_size=1, max_size=2,
        statement_cache_size=0, init=_register_vector,
    )
    anth = anthropic.AsyncAnthropic(api_key=cfg.anthropic_api_key)
    embedder = _load_embedder(cfg)

    built = failed = skipped = 0
    try:
        if cfg.refresh_older_than_days is not None and cfg.rebuild:
            log.warning(
                "PROFILE_REFRESH_OLDER_THAN_DAYS and PROFILE_REBUILD both set; "
                "refresh mode wins, rebuild ignored"
            )
        candidates = await _fetch_candidates(
            pool,
            limit=cfg.limit or 10000,
            rebuild=cfg.rebuild,
            refresh_older_than_days=cfg.refresh_older_than_days,
        )
        mode = (
            f"refresh_older_than_days={cfg.refresh_older_than_days}"
            if cfg.refresh_older_than_days is not None
            else ("rebuild" if cfg.rebuild else "first-build-only")
        )
        log.info(
            "profile builder: %d candidates (mode=%s model=%s recent_per_person=%d dry_run=%s)",
            len(candidates), mode, cfg.model, cfg.messages_per_profile,
            cfg.dry_run,
        )

        for c in candidates:
            ctx = await _fetch_person_context(
                pool, c["person_id"], recent_n=cfg.messages_per_profile
            )
            if not ctx["messages"]:
                log.info("skip %s: no non-empty messages yet", c["display_name"])
                skipped += 1
                # If a profile already exists, stamp its signature so a too-thin
                # contact flagged for rebuild (e.g. enriched but only 1 empty/voice
                # message) isn't re-selected on every run. No-op when no profile row.
                async with pool.acquire() as conn:
                    await conn.execute(
                        f"UPDATE memory.profile SET input_sig = {_sig_sql('$1::uuid')} "
                        "WHERE person_id = $1::uuid",
                        c["person_id"],
                    )
                continue

            payload = prompt.render_user_prompt(
                display_name=c["display_name"],
                telegram_username=c["telegram_username"],
                telegram_id=c["telegram_id"],
                telegram_bio=c["telegram_bio"],
                message_count=ctx["stats"]["message_count"],
                first_message_at=ctx["stats"]["first_message_at"],
                last_message_at=ctx["stats"]["last_message_at"],
                inbound_count=ctx["stats"]["inbound_count"],
                outbound_count=ctx["stats"]["outbound_count"],
                channels=list(ctx["stats"]["channels"] or []),
                recent_messages=ctx["messages"],
                candidate_signals=ctx["signals"],
                bios=ctx["bios"],
                self_label=cfg.self_user_label,
            )

            if cfg.dry_run:
                log.info("DRY %s — prompt:\n%s", c["display_name"], payload[:600])
                continue

            sensitive = bool(c["sensitive"])
            try:
                if sensitive:
                    # Sensitive contact: on-box Ollama, narrative only (the
                    # structured identifier extraction is skipped — see
                    # local_llm). Fail-closed: never falls back to cloud.
                    log.info("route=local person=%s reason=sensitive", c["display_name"])
                    result = {
                        "summary": await asyncio.to_thread(
                            local_llm.ollama_summary,
                            url=cfg.ollama_url, model=cfg.ollama_model,
                            payload=payload, timeout=cfg.ollama_timeout),
                        "structured": None,
                    }
                else:
                    result = await _generate(anth, cfg, payload)
            except Exception:
                if sensitive:
                    log.exception("local profile FAILED for %s — skipped, no cloud fallback",
                                  c["display_name"])
                else:
                    log.exception("LLM call failed for %s", c["display_name"])
                failed += 1
                continue
            if not result or not result.get("summary"):
                log.warning("no %s output for %s",
                            "local" if sensitive else "tool", c["display_name"])
                failed += 1
                continue

            summary = (result.get("summary") or "").strip()
            structured = result.get("structured") or None

            try:
                vec = await asyncio.to_thread(_embed, embedder, summary)
            except Exception:
                log.exception("embed failed for %s", c["display_name"])
                # Still persist the summary; embedding can be regenerated later.
                vec = None

            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO memory.profile
                      (person_id, summary, structured, embedding, embedding_model,
                       source_interaction_count, last_built_at, input_sig)
                    VALUES ($1::uuid, $2, $3::jsonb, $4, $5, $6, now(), """
                    + _sig_sql("$1::uuid") + """)
                    ON CONFLICT (person_id) DO UPDATE SET
                      summary = EXCLUDED.summary,
                      structured = EXCLUDED.structured,
                      embedding = EXCLUDED.embedding,
                      embedding_model = EXCLUDED.embedding_model,
                      source_interaction_count = EXCLUDED.source_interaction_count,
                      last_built_at = now(),
                      input_sig = EXCLUDED.input_sig
                    """,
                    c["person_id"], summary,
                    json.dumps(structured, default=str) if structured else None,
                    vec, cfg.embedding_model,
                    int(c["message_count"]),
                )
            built += 1
            if built % 10 == 0:
                log.info("progress: built=%d failed=%d skipped=%d",
                         built, failed, skipped)

        log.info("done: built=%d failed=%d skipped=%d", built, failed, skipped)
        return 0 if failed == 0 else 1
    finally:
        await pool.close()
