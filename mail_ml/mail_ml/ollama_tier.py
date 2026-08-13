"""Local-LLM refinement tier (Personal OS Phase 5): re-classify the messages the
SetFit model is UNSURE about (confidence below a threshold) with the on-box
Ollama model. Highest accuracy, highest latency — so it runs only on the
low-confidence slice, in bounded batches, on the classify timer. All inference
stays on the droplet; nothing leaves the box.
"""
from __future__ import annotations

import json
import logging
import re

import httpx

from . import labeling

log = logging.getLogger("mail_ml.ollama")

_PROMPT = """Classify this email into exactly one of: newsletter, transactional, personal.

Definitions:
- newsletter: broadcast marketing/editorial/subscription mail sent to many people.
- transactional: automated one-to-one system mail (receipts, invoices, alerts, sign-ins, notifications).
- personal: a real human wrote it to the recipient.

Reply ONLY with JSON: {{"class": "newsletter|transactional|personal"}}

From: {sender}
Subject: {subject}
Body (truncated): {body}"""


def parse_verdict(text: str) -> str | None:
    """Extract a valid class from the model's reply. JSON first; falls back to
    the first class word mentioned. None = unusable (caller keeps SetFit's)."""
    try:
        v = json.loads(text)
        c = str(v.get("class", "")).strip().lower()
        if c in labeling.CLASSES:
            return c
    except Exception:  # noqa: BLE001
        pass
    m = re.search(r"\b(newsletter|transactional|personal)\b", text.lower())
    return m.group(1) if m else None


def classify_message(*, subject: str | None, body: str | None, from_address: str | None,
                     url: str, model: str, client: httpx.Client,
                     timeout: float = 120.0) -> str | None:
    """One classification via Ollama /api/chat (format=json, temp 0). Returns a
    valid class or None on any failure — a miss must never break the batch."""
    prompt = _PROMPT.format(
        sender=(from_address or "")[:120],
        subject=re.sub(r"\s+", " ", subject or "")[:200],
        body=re.sub(r"\s+", " ", body or "")[:700])
    try:
        r = client.post(f"{url.rstrip('/')}/api/chat", timeout=timeout, json={
            "model": model, "stream": False, "format": "json",
            "options": {"temperature": 0, "num_predict": 30},
            "messages": [{"role": "user", "content": prompt}],
        })
        r.raise_for_status()
        return parse_verdict(r.json().get("message", {}).get("content", ""))
    except Exception as e:  # noqa: BLE001
        log.warning("ollama classify failed: %s", e)
        return None
