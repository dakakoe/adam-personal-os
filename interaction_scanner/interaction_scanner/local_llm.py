"""Local Ollama chat + tolerant JSON parsing for sensitive-contact routing.

A contact marked `sensitive` must never have their messages sent to a cloud
provider: the scanner calls the on-box Ollama with the SAME system prompt and
an in-prompt JSON schema replacing Anthropic's forced tool call. Fail-closed —
any error here means the thread is skipped (re-scanned next run), NEVER
retried against the cloud.
"""
from __future__ import annotations

import json
from typing import Any

import httpx

# Replaces the record_conversation_items tool schema for a model without
# forced tool-use. format=json guarantees syntax, not schema — parse_items
# below is deliberately tolerant.
JSON_INSTRUCTION = """

Reply ONLY with a JSON object of this exact shape (empty arrays when nothing \
is actionable):
{"tasks": [{"title": "...", "due_hint": "", "last_discussed": "YYYY-MM-DD HH:MM"}],
 "opportunities": [{"title": "...", "stage": "intro|conversations|mou|contract|active", "value": "", "last_discussed": "YYYY-MM-DD HH:MM"}]}"""

_STAGES = {"intro", "conversations", "mou", "contract", "active"}


def ollama_chat(*, url: str, model: str, prompt: str, system: str | None = None,
                json_mode: bool = False, temperature: float = 0.0,
                num_predict: int = 700, timeout: float = 180.0) -> str:
    """One chat completion against the local Ollama. Raises on any transport/
    HTTP failure — the caller decides what fail-closed means."""
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    body: dict[str, Any] = {
        "model": model, "messages": messages, "stream": False,
        "options": {"temperature": temperature, "num_predict": num_predict},
    }
    if json_mode:
        body["format"] = "json"
    resp = httpx.post(f"{url.rstrip('/')}/api/chat", json=body, timeout=timeout)
    resp.raise_for_status()
    return ((resp.json().get("message") or {}).get("content") or "").strip()


def parse_items(text: str) -> dict[str, Any] | None:
    """Tolerant parse of the local model's reply into the same shape the
    Anthropic tool call returns: {"tasks": [...], "opportunities": [...]}.
    Pure — tested without a model. Returns None only for unusable output."""
    try:
        data = json.loads(text or "")
    except (ValueError, TypeError):
        return None
    if isinstance(data, list):          # bare list → assume they're tasks
        data = {"tasks": data, "opportunities": []}
    if not isinstance(data, dict):
        return None

    def _items(key: str) -> list[dict[str, Any]]:
        raw = data.get(key) or []
        if not isinstance(raw, list):
            return []
        out = []
        for it in raw:
            if not isinstance(it, dict):
                continue
            title = (it.get("title") or "").strip() if isinstance(it.get("title"), str) else ""
            if not title:
                continue           # titleless junk
            out.append(it)
        return out

    tasks = _items("tasks")
    opps = _items("opportunities")
    for it in opps:                     # invalid/missing stage → safest default
        stage = it.get("stage")
        if not isinstance(stage, str) or stage.strip().lower() not in _STAGES:
            it["stage"] = "intro"
        else:
            it["stage"] = stage.strip().lower()
    return {"tasks": tasks, "opportunities": opps}
