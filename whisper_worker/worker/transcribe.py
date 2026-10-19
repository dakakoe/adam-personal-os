from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)


def load_model(cfg: "Any") -> Any:
    """Construct a faster-whisper model once at startup. Heavy import is
    deferred so `python -m worker --help`-style introspection doesn't pay
    for it."""
    from faster_whisper import WhisperModel

    log.info(
        "loading model=%s compute_type=%s cpu_threads=%d num_workers=%d cache=%s",
        cfg.model, cfg.compute_type, cfg.cpu_threads, cfg.num_workers,
        cfg.model_cache_dir,
    )
    model = WhisperModel(
        cfg.model,
        device="cpu",
        compute_type=cfg.compute_type,
        cpu_threads=cfg.cpu_threads,
        num_workers=cfg.num_workers,
        download_root=cfg.model_cache_dir,
    )
    return model


def _transcribe_sync(model: Any, file_path: str, language: str | None) -> tuple[str, str | None]:
    """The actual blocking call. Returns (text, detected_language). Joins
    Whisper's segment generator into one string."""
    segments, info = model.transcribe(
        file_path,
        language=language,           # None => auto-detect
        beam_size=5,                 # default; quality vs speed
        vad_filter=True,             # skip silence
        vad_parameters={"min_silence_duration_ms": 500},
    )
    parts: list[str] = []
    for seg in segments:
        t = (seg.text or "").strip()
        if t:
            parts.append(t)
    return " ".join(parts).strip(), info.language


async def transcribe_file(
    model: Any, file_path: str, language: str | None
) -> tuple[str, str | None]:
    """Run the blocking faster-whisper call in a thread so we don't pin
    the event loop. Returns (text, detected_language)."""
    return await asyncio.to_thread(_transcribe_sync, model, file_path, language)
