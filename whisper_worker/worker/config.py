from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    db_url: str
    model: str          # faster-whisper model name: tiny/base/small/medium/large-v3
    compute_type: str   # int8 is the right pick on CPU
    cpu_threads: int    # 0 = let CTranslate2 decide
    num_workers: int    # CTranslate2 parallelism (within one transcription)
    language: str | None
    voice_dir: str
    model_cache_dir: str
    poll_interval_sec: int
    batch_size: int


def load() -> Config:
    return Config(
        db_url=os.environ.get("WHISPER_DATABASE_URL") or _build_db_url(),
        # 'base' is the sweet spot for personal voice on a 4-vCPU CPU:
        # 5-8x realtime, ~150MB model, multilingual.
        model=os.environ.get("WHISPER_MODEL", "base"),
        compute_type=os.environ.get("WHISPER_COMPUTE_TYPE", "int8"),
        cpu_threads=int(os.environ.get("WHISPER_CPU_THREADS", "2")),
        num_workers=int(os.environ.get("WHISPER_NUM_WORKERS", "1")),
        # None == auto-detect (Whisper guesses from audio).
        language=os.environ.get("WHISPER_LANGUAGE") or None,
        voice_dir=os.environ.get("WHISPER_VOICE_DIR", "/srv/memory/data/voice"),
        # Where faster-whisper caches model files (~150MB for 'base').
        model_cache_dir=os.environ.get(
            "WHISPER_MODEL_CACHE", "/srv/memory/data/whisper-models"
        ),
        poll_interval_sec=int(os.environ.get("WHISPER_POLL_INTERVAL", "60")),
        batch_size=int(os.environ.get("WHISPER_BATCH_SIZE", "20")),
    )


def _required(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise SystemExit(f"missing required env: {name}")
    return val


def _build_db_url() -> str:
    user = _required("POSTGRES_USER")
    pw = _required("POSTGRES_PASSWORD")
    db = _required("POSTGRES_DB")
    host = os.environ.get("POSTGRES_HOST", "127.0.0.1")
    port = os.environ.get("POSTGRES_PORT", "5432")
    return f"postgres://{user}:{pw}@{host}:{port}/{db}"
