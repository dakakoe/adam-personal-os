from __future__ import annotations

from typing import Any

# The profile builder asks Haiku to call a single tool, `save_profile`, with
# BOTH the narrative summary and a structured JSON extract. Forcing tool-use
# means we don't have to parse text — the SDK hands us {"summary": str,
# "structured": dict} directly, and a malformed run fails loudly instead of
# silently writing garbage.

SYSTEM_PROMPT = """You are filing concise contact-memory records for a personal CRM.

For each contact, call the `save_profile` tool exactly once with:

1. `summary` — ONE short paragraph (3-6 sentences) describing this person and the user's relationship with them. Be specific and factual; use evidence from the provided messages. No fluff, no greetings, no "Based on the messages…" preamble.

   Cover (when supported by the data):
   - Who they appear to be (name, profession or context if visible)
   - The texture of the relationship (close friend / colleague / acquaintance / one-time contact)
   - Topics frequently discussed
   - Anything time-bound that matters (status changes, recent life events)

   AUTHORITY OF SOURCES: a source bio (a LinkedIn "role", a Google-contacts
   org/title) states the person's CURRENT, present-day position — treat it as
   authoritative and lead the "who they are" with it. A role you can only infer
   from OLD conversations is historical; if it conflicts with a source bio,
   prefer the bio and, if useful, note the earlier role as past ("was a … back
   when you spoke, now …"). Don't flatten someone's current senior title into a
   stale one just because the chats predate it.

   Synthesize, don't quote. Write in English regardless of source language.

2. `structured` — extract concrete facts. Use null / empty arrays liberally when the data doesn't support a confident value. CRITICALLY important: distinguish between identifiers that BELONG to this contact vs. third-party identifiers they shared (e.g. forwarding someone else's email, mentioning another person's LinkedIn).

   - `personal_emails`: emails that ARE this contact's own (their address). Exclude addresses they mentioned but that belong to other people.
   - `personal_social`: their own handles. Exclude handles of people they discussed.
   - `current_company` / `current_role`: only if confidently inferable.
   - `past_companies`: prior employers mentioned.
   - `languages`: languages they personally communicate in.

Do not speculate. When uncertain, leave the field null or the array empty.
"""


SAVE_PROFILE_TOOL = {
    "name": "save_profile",
    "description": "Persist this contact's narrative summary and structured facts.",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "3-6 sentence English narrative paragraph.",
            },
            "structured": {
                "type": "object",
                "properties": {
                    "current_company": {"type": ["string", "null"]},
                    "current_role": {"type": ["string", "null"]},
                    "past_companies": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "personal_emails": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Emails that belong to THIS contact, not third parties.",
                    },
                    "personal_social": {
                        "type": "object",
                        "properties": {
                            "linkedin": {"type": ["string", "null"]},
                            "x": {"type": ["string", "null"]},
                            "instagram": {"type": ["string", "null"]},
                            "github": {"type": ["string", "null"]},
                            "website": {"type": ["string", "null"]},
                            "telegram": {"type": ["string", "null"]},
                        },
                        "additionalProperties": False,
                    },
                    "languages": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": [
                    "current_company", "current_role", "past_companies",
                    "personal_emails", "personal_social", "languages",
                ],
                "additionalProperties": False,
            },
        },
        "required": ["summary", "structured"],
    },
}


def render_user_prompt(
    *,
    display_name: str,
    telegram_username: str | None,
    telegram_id: str | None,
    telegram_bio: str | None,
    message_count: int,
    first_message_at,
    last_message_at,
    inbound_count: int,
    outbound_count: int,
    channels: list[str],
    recent_messages: list[dict[str, Any]],
    candidate_signals: list[dict[str, Any]],
    bios: list[dict[str, Any]] | None,
    self_label: str,
) -> str:
    """Build the per-person user-turn payload. Stats first, then per-source
    bios (a labeled list so the LLM can synthesize provenance-aware), then
    the list of candidate signals the regex extractor found (so the LLM
    can adjudicate which are personal vs. third-party), then a chronological
    excerpt of recent messages annotated with direction.

    `telegram_bio` is left as a parameter for backwards compat but is now
    covered by the `bios` array; rendered separately ONLY when bios is
    empty (e.g. running with an old context dict)."""
    lines: list[str] = []
    lines.append(f"Contact: {display_name}")
    if telegram_username:
        lines.append(f"Telegram: @{telegram_username}")
    if telegram_id:
        lines.append(f"Telegram ID: {telegram_id}")
    lines.append("")

    if bios:
        lines.append("Bios from each source (synthesize across — they may agree, disagree, or complement):")
        for b in bios:
            label = f"{b.get('source')}/{b.get('kind')}"
            text = (b.get("text") or "").strip().replace("\n", " ")
            if len(text) > 400:
                text = text[:399] + "…"
            lines.append(f"- [{label}] {text}")
        lines.append("")
    elif telegram_bio:
        # Legacy code path — bios array missing for some reason.
        lines.append("Telegram bio (self-asserted):")
        lines.append(f"  {telegram_bio}")
        lines.append("")

    lines.append("Aggregate stats:")
    lines.append(f"- total interactions: {message_count}")
    lines.append(
        f"- inbound (from them): {inbound_count}   outbound (from {self_label}): {outbound_count}"
    )
    if first_message_at and last_message_at:
        lines.append(
            f"- first interaction: {first_message_at.isoformat()}  "
            f"last: {last_message_at.isoformat()}"
        )
    if channels:
        lines.append(f"- channels used: {', '.join(channels)}")
    lines.append("")

    if candidate_signals:
        lines.append("Identifier candidates extracted from messages and bio (you decide which are theirs vs. shared):")
        # Group by type for readability
        by_type: dict[str, list[str]] = {}
        for s in candidate_signals:
            by_type.setdefault(s["signal_type"], []).append(
                f"{s['value']} (from {s['source']})"
            )
        for sig_type in sorted(by_type):
            lines.append(f"- {sig_type}:")
            for v in by_type[sig_type][:20]:  # cap per type
                lines.append(f"    - {v}")
        lines.append("")

    lines.append("Most recent messages (oldest -> newest in this excerpt):")
    lines.append("```")
    for m in recent_messages:
        who = "them" if m["direction"] == "inbound" else self_label
        ts = m["occurred_at"].strftime("%Y-%m-%d") if m["occurred_at"] else ""
        body = (m["body"] or "").strip().replace("\n", " ")
        if len(body) > 280:
            body = body[:279] + "…"
        lines.append(f"[{ts}] {who}: {body}")
    lines.append("```")
    return "\n".join(lines)
