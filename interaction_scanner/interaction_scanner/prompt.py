from __future__ import annotations

from typing import Any

SYSTEM_PROMPT = """You scan a recent 1:1 conversation between the user (a busy \
crypto/Web3 founder & BD operator) and one contact, and surface ONLY concrete, \
actionable items the user would want tracked. Precision over recall — an empty \
result is better than noise. Most messages contain nothing actionable.

Extract:
- tasks: a specific thing the USER needs to do (follow up, send, prepare, \
review, decide). Not the other person's chores. Not vague intentions.
- opportunities: a deal, partnership, sponsorship, intro, revenue path, or \
"something we could do together" being discussed with this contact. Pick the \
closest stage: intro / conversations / mou / contract / active.

Hard rules:
- If the conversation is small talk, logistics, or already-completed actions, \
return empty arrays. Do NOT manufacture items.
- Titles must be specific and self-contained (someone reading just the title \
should know what to do). Bad: "follow up". Good: "Send Alex the revised \
Q3 pricing proposal".
- Do not restate the same item as both a task and an opportunity.
- ALREADY TRACKED: if an item is already covered by the user's tracked list \
(shown below when present), DO NOT propose it again. Treat as a duplicate ANY \
item that shares the same core action or goal with a tracked one — even if \
reworded, expanded with extra detail, rescoped, narrowed, merged, or split. \
"Connect X to Y" and "Connect X to Y team for partnership in Z" are the SAME \
item. Only surface items whose core action is genuinely absent from the list. \
When in doubt, leave it out.
"""


SAVE_TOOL = {
    "name": "record_conversation_items",
    "description": "Record actionable tasks and opportunities found in the conversation.",
    "input_schema": {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "due_hint": {"type": "string", "description": "Timing hint if any, else empty."},
                        "last_discussed": {"type": "string", "description": "Exact UTC timestamp (YYYY-MM-DD HH:MM) of the message where this was discussed, copied from the [timestamp] shown. Empty if unclear."},
                    },
                    "required": ["title"],
                },
            },
            "opportunities": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "stage": {
                            "type": "string",
                            "enum": ["intro", "conversations", "mou", "contract", "active"],
                        },
                        "value": {"type": "string", "description": "Money/outcome if mentioned, else empty."},
                        "last_discussed": {"type": "string", "description": "Exact UTC timestamp (YYYY-MM-DD HH:MM) of the message where this was discussed, copied from the [timestamp] shown. Empty if unclear."},
                    },
                    "required": ["title", "stage"],
                },
            },
        },
        "required": ["tasks", "opportunities"],
    },
}


def render(
    *, contact_name: str, channel: str, messages: list[dict[str, Any]],
    self_label: str, existing_items: list[str] | None = None,
) -> str:
    lines = [
        f"Conversation with: {contact_name} (channel: {channel})",
        f"'{self_label}' = the user; 'them' = {contact_name}.",
    ]
    if existing_items:
        lines.append("")
        lines.append(
            "ALREADY TRACKED for this contact (do NOT re-propose these, even "
            "reworded or rescoped — only surface genuinely new items):"
        )
        for title in existing_items:
            lines.append(f"  - {title}")
    lines.append("")
    lines.append("Recent messages (chronological, with [UTC timestamp]):")
    for m in messages:
        who = self_label if m["direction"] == "outbound" else "them"
        body = (m["body"] or "").strip().replace("\n", " ")
        if len(body) > 500:
            body = body[:499] + "…"
        occ = m.get("occurred_at")
        ts = occ.strftime("%Y-%m-%d %H:%M") if occ is not None and hasattr(occ, "strftime") else "?"
        lines.append(f"  [{ts}] [{who}] {body}")
    return "\n".join(lines)
