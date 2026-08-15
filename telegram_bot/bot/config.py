from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    # Telegram bot (same token the alerter sends with; this worker is the sole
    # getUpdates consumer — sending and polling on one token don't conflict).
    bot_token: str
    allowed_chat_id: int           # primary owner (the user) — default reply target
    allowed_chats: dict[int, str]  # chat_id → owner ('me'|'wife'|'son') security allow-list
    long_poll_timeout_s: int

    # merge_api (all DB/LLM/business logic lives there; this worker is thin).
    merge_api_base: str
    bearer_token: str

    # voice → faster-whisper (reuse the on-box model + shared cache).
    voice_dir: str
    model: str
    compute_type: str
    cpu_threads: int
    num_workers: int
    language: str | None
    model_cache_dir: str

    state_path: str
    http_timeout_s: float


def load() -> Config:
    long_poll = int(os.environ.get("BOT_LONG_POLL_TIMEOUT", "50"))
    me_chat = int(os.environ.get("BOT_ALLOWED_CHAT_ID") or _required("ALERT_TG_CHAT_ID"))
    chats = {me_chat: "me"}
    wife = os.environ.get("BOT_WIFE_CHAT_ID")
    if wife:
        chats[int(wife)] = "wife"
    son = os.environ.get("BOT_SON_CHAT_ID")
    if son:
        chats[int(son)] = "son"
    return Config(
        bot_token=_required("ALERT_TG_BOT_TOKEN"),
        allowed_chat_id=me_chat,
        allowed_chats=chats,
        long_poll_timeout_s=long_poll,
        merge_api_base=os.environ.get("BOT_MERGE_API_BASE", "http://127.0.0.1:9100"),
        bearer_token=_required("MERGE_API_BEARER_TOKEN"),
        voice_dir=os.environ.get("BOT_VOICE_DIR", "/srv/memory/data/bot_voice"),
        model=os.environ.get("WHISPER_MODEL", "base"),
        compute_type=os.environ.get("WHISPER_COMPUTE_TYPE", "int8"),
        # one thread by default — single short note, latency over throughput,
        # and we share the box with the batch whisper_worker.
        cpu_threads=int(os.environ.get("BOT_WHISPER_CPU_THREADS", "1")),
        num_workers=int(os.environ.get("WHISPER_NUM_WORKERS", "1")),
        language=os.environ.get("WHISPER_LANGUAGE") or None,
        model_cache_dir=os.environ.get("WHISPER_MODEL_CACHE", "/srv/memory/data/whisper-models"),
        state_path=os.environ.get("BOT_STATE_PATH", "/srv/memory/state/telegram_bot.json"),
        http_timeout_s=float(long_poll + 15),
    )


def _required(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise SystemExit(f"missing required env: {name}")
    return val
