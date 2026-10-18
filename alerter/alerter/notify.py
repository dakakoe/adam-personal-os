from __future__ import annotations

import logging

import httpx

from .config import Config

log = logging.getLogger(__name__)


def send_telegram(cfg: Config, text: str) -> bool:
    """Send a plain-text message via the Telegram Bot API. Returns True on
    success. No parse_mode — plain text avoids markdown-escaping foot-guns;
    emoji and the → arrow render fine as-is."""
    url = f"https://api.telegram.org/bot{cfg.tg_bot_token}/sendMessage"
    try:
        resp = httpx.post(
            url,
            json={
                "chat_id": cfg.tg_chat_id,
                "text": text,
                "disable_web_page_preview": True,
            },
            timeout=cfg.timeout_s,
        )
        resp.raise_for_status()
    except Exception as e:  # noqa: BLE001
        # Surface the Telegram error body — it's the usual culprit (bad token,
        # or "chat not found" until the user has messaged the bot once).
        body = ""
        if isinstance(e, httpx.HTTPStatusError):
            body = f" — {e.response.text[:200]}"
        log.error("telegram send failed: %s%s", e, body)
        return False
    return True
