from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import asyncpg

from . import queries
from .config import Config

log = logging.getLogger(__name__)


SUMMARY_SYSTEM = """You are writing a short, factual daily digest for a personal CRM.
The user has just woken up and wants a tight paragraph (3-5 sentences) summarising the last 24 hours of conversations. Be specific and concrete — name people, name topics, mention emotional or logistical signals when they appear. No fluff, no greetings, no "Here is a summary". Write in English regardless of the source languages.
"""


def _render(
    *,
    digest_date: datetime,
    window_hours: int,
    stats: dict[str, Any],
    top: list[dict[str, Any]],
    voice: list[dict[str, Any]],
    new_contacts: list[dict[str, Any]],
    re_eng: list[dict[str, Any]],
    totals: dict[str, Any],
    narrative: str | None,
) -> str:
    lines: list[str] = []
    lines.append(f"# Memory digest — {digest_date.strftime('%Y-%m-%d')}")
    lines.append("")
    lines.append(f"_Window: last {window_hours}h up to {digest_date.isoformat(timespec='seconds')}_")
    lines.append("")

    lines.append("## What happened")
    if narrative:
        lines.append("")
        lines.append(narrative.strip())
    elif stats["messages"] == 0:
        lines.append("")
        lines.append("_Nothing in the last window._")
    else:
        lines.append("")
        lines.append(
            f"{stats['messages']} messages "
            f"({stats['inbound']} in / {stats['outbound']} out) "
            f"with {stats['people']} contacts."
        )
    lines.append("")

    lines.append("## Numbers")
    lines.append("")
    lines.append(
        f"- Messages: **{stats['messages']}** "
        f"(in {stats['inbound']} / out {stats['outbound']})"
    )
    lines.append(f"- Distinct contacts: **{stats['people']}**")
    if stats["voice_msgs"]:
        lines.append(
            f"- Voice messages: {stats['voice_msgs']} "
            f"({stats['voice_transcribed']} transcribed)"
        )
    lines.append("")

    if top:
        lines.append("## Top contacts in window")
        lines.append("")
        lines.append("| Person | Total | Inbound | Outbound |")
        lines.append("|---|--:|--:|--:|")
        for r in top:
            lines.append(
                f"| {r['display_name']} | {r['messages']} | {r['inbound']} | {r['outbound']} |"
            )
        lines.append("")

    if voice:
        lines.append("## Voice transcripts that landed in the window")
        lines.append("")
        lines.append("_(may be older voice notes — included when their transcript becomes available)_")
        lines.append("")
        for r in voice:
            ts = r["occurred_at"].strftime("%Y-%m-%d %H:%M") if r["occurred_at"] else "?"
            preview = (r["preview"] or "").replace("\n", " ").strip()
            lines.append(f"- **{r['display_name']}** ({ts}): {preview}")
        lines.append("")

    if new_contacts:
        lines.append("## New people")
        lines.append("")
        for r in new_contacts:
            ts = r["first_at"].strftime("%Y-%m-%d %H:%M") if r["first_at"] else "?"
            lines.append(f"- **{r['display_name']}** — first contact at {ts}, {r['total']} messages so far")
        lines.append("")

    if re_eng:
        lines.append("## Re-engagement candidates")
        lines.append("")
        lines.append("_Active historically (>50 msgs total) but quiet for 30-180 days._")
        lines.append("")
        for r in re_eng:
            days = int(r["gap"].total_seconds() // 86400)
            lines.append(
                f"- **{r['display_name']}** — last interaction {days}d ago, "
                f"{r['total']} total"
            )
        lines.append("")

    lines.append("## Memory totals")
    lines.append("")
    lines.append(f"- {totals['persons']:,} contacts")
    lines.append(f"- {totals['interactions']:,} interactions ({totals['bodies']:,} with text body)")
    lines.append(f"- {totals['profiles']:,} LLM profiles built")
    lines.append(f"- {totals['embeddings']:,} interaction embeddings")
    lines.append("")

    return "\n".join(lines)


async def _llm_summary(cfg: Config, payload: str) -> str | None:
    """Narrative over the last 24h of raw message text — the digest's payload
    is the single most private prompt in the system, so it runs WHOLESALE on
    the local Ollama (sovereignty routing; Anthropic no longer sees it).
    Any failure → None → the stats-only digest renders as before."""
    import httpx
    try:
        # 600s: ~6k-char prefill on CPU is minutes, and Ollama serializes
        # concurrent callers (scanner/refine can be queued ahead of us) — this
        # is a nightly cron, latency is irrelevant, only the fallback isn't.
        async with httpx.AsyncClient(timeout=600) as hc:
            resp = await hc.post(
                f"{cfg.ollama_url.rstrip('/')}/api/chat",
                json={
                    "model": cfg.ollama_model, "stream": False,
                    "messages": [{"role": "system", "content": SUMMARY_SYSTEM},
                                 {"role": "user", "content": payload}],
                    "options": {"temperature": 0.2, "num_predict": 350},
                })
            resp.raise_for_status()
            return (((resp.json().get("message") or {}).get("content")) or "").strip() or None
    except Exception:
        log.exception("local digest summary failed; continuing without it")
        return None


async def _build_narrative_payload(
    conn: asyncpg.Connection, since: datetime, *, max_chars: int = 6000
) -> str:
    rows = await conn.fetch(
        """
        SELECT i.occurred_at, i.direction, p.display_name, i.body
        FROM canonical.interaction i
        JOIN canonical.person p ON p.id = i.person_id
        WHERE i.occurred_at >= $1
          AND i.body IS NOT NULL AND length(i.body) > 0
        ORDER BY i.occurred_at ASC
        """,
        since,
    )
    out: list[str] = []
    used = 0
    for r in rows:
        who = "them" if r["direction"] == "inbound" else "me"
        ts = r["occurred_at"].strftime("%H:%M") if r["occurred_at"] else ""
        body = (r["body"] or "").strip().replace("\n", " ")
        if len(body) > 220:
            body = body[:219] + "…"
        line = f"[{ts}] {r['display_name']} <- {who}: {body}\n"
        if used + len(line) > max_chars:
            out.append(f"… ({len(rows) - len(out)} more messages truncated)")
            break
        out.append(line)
        used += len(line)
    return "".join(out)


async def run(cfg: Config) -> int:
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=cfg.window_hours)

    pool = await asyncpg.create_pool(
        cfg.db_url, min_size=1, max_size=2, statement_cache_size=0
    )
    try:
        async with pool.acquire() as conn:
            stats = await queries.overall_stats(conn, since)
            top = await queries.top_contacts(conn, since, limit=10)
            voice = await queries.new_voice_transcripts(conn, since, limit=5)
            new_contacts = await queries.newest_contacts_in_window(conn, since, limit=5)
            re_eng = await queries.re_engagement_candidates(conn, limit=5)
            totals = await queries.memory_totals(conn)
            narrative_payload = (
                await _build_narrative_payload(conn, since)
                if stats["messages"] > 0
                else ""
            )

        narrative = (
            await _llm_summary(cfg, narrative_payload)
            if narrative_payload.strip()
            else None
        )

        md = _render(
            digest_date=now,
            window_hours=cfg.window_hours,
            stats=stats,
            top=top,
            voice=voice,
            new_contacts=new_contacts,
            re_eng=re_eng,
            totals=totals,
            narrative=narrative,
        )

        out_dir = Path(cfg.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"digest-{now.strftime('%Y-%m-%d')}.md"
        out_path.write_text(md, encoding="utf-8")
        log.info("wrote %s (%d bytes, %d messages in window)",
                 out_path, out_path.stat().st_size, stats["messages"])
        # Echo to stdout too — handy when run manually.
        print(md)
        return 0
    finally:
        await pool.close()
