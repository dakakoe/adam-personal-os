"""Local faster-whisper transcription — copied from whisper_worker/worker/
transcribe.py (cross-package import isn't on the path). Reads the .oga/.ogg
(opus) files Telegram serves, identical to the Telethon voice notes."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)


def load_model(cfg: "Any") -> Any:
    """Construct a faster-whisper model once at startup (heavy import deferred)."""
    from faster_whisper import WhisperModel

    log.info(
        "loading model=%s compute_type=%s cpu_threads=%d cache=%s",
        cfg.model, cfg.compute_type, cfg.cpu_threads, cfg.model_cache_dir,
    )
    return WhisperModel(
        cfg.model,
        device="cpu",
        compute_type=cfg.compute_type,
        cpu_threads=cfg.cpu_threads,
        num_workers=cfg.num_workers,
        download_root=cfg.model_cache_dir,
    )


def _transcribe_sync(model: Any, file_path: str, language: str | None) -> tuple[str, str | None]:
    segments, info = model.transcribe(
        file_path,
        language=language,           # None => auto-detect
        beam_size=5,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
    )
    parts = [(seg.text or "").strip() for seg in segments]
    return " ".join(p for p in parts if p).strip(), info.language


async def transcribe_file(model: Any, file_path: str, language: str | None) -> tuple[str, str | None]:
    """Run the blocking faster-whisper call in a thread."""
    return await asyncio.to_thread(_transcribe_sync, model, file_path, language)
