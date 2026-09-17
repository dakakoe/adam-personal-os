"""Local Ollama path for sensitive contacts (their messages never go to a
cloud provider). Narrative summary ONLY — the structured identifier
extraction (personal_emails etc.) is deliberately skipped locally: a 3B model
mis-adjudicating "their address vs an address they mentioned" would poison
identity data. Fail-closed: any error here means the contact is skipped this
run, never retried against the cloud.
"""
from __future__ import annotations

import httpx

# The `summary` half of prompt.SYSTEM_PROMPT, reworded only where it referred
# to the save_profile tool call.
LOCAL_SYSTEM_PROMPT = """You are filing concise contact-memory records for a personal CRM.

Write ONE short paragraph (3-6 sentences) describing this person and the user's relationship with them. Be specific and factual; use evidence from the provided messages. No fluff, no greetings, no "Based on the messages…" preamble.

Cover (when supported by the data):
- Who they appear to be (name, profession or context if visible)
- The texture of the relationship (close friend / colleague / acquaintance / one-time contact)
- Topics frequently discussed
- Anything time-bound that matters (status changes, recent life events)

Synthesize, don't quote. Write in English regardless of source language. Reply with ONLY the paragraph — no headings, no lists."""


def ollama_summary(*, url: str, model: str, payload: str,
                   timeout: float = 180.0) -> str:
    """One free-text completion against the local Ollama. Raises on failure."""
    resp = httpx.post(
        f"{url.rstrip('/')}/api/chat",
        json={
            "model": model, "stream": False,
            "messages": [
                {"role": "system", "content": LOCAL_SYSTEM_PROMPT},
                {"role": "user", "content": payload},
            ],
            "options": {"temperature": 0.2, "num_predict": 300},
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    return ((resp.json().get("message") or {}).get("content") or "").strip()
