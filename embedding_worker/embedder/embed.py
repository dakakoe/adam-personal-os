from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)


def load_model(cfg: "Any") -> Any:
    """Bring in torch + sentence-transformers lazily so module import is cheap
    and we don't pay the heavy startup tax in tests / --help."""
    import torch
    from sentence_transformers import SentenceTransformer

    torch.set_num_threads(cfg.cpu_threads)
    # MKL/OpenMP also respect these caps — protects us when whisper is
    # concurrently using the other cores.
    log.info(
        "loading model=%s threads=%d cache=%s",
        cfg.model_name, cfg.cpu_threads, cfg.cache_dir,
    )
    model = SentenceTransformer(
        cfg.model_name,
        device="cpu",
        cache_folder=cfg.cache_dir,
    )
    return model


def _encode_sync(model: Any, texts: list[str]) -> list[list[float]]:
    """The blocking encode call. E5 family expects a 'passage: ' prefix
    for document-side embeddings (and 'query: ' for queries — we'll
    apply that on the MCP side)."""
    prefixed = [f"passage: {t}" for t in texts]
    arr = model.encode(
        prefixed,
        batch_size=len(prefixed),  # already chunked by caller
        normalize_embeddings=True,  # so cosine == dot product
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    return [row.tolist() for row in arr]


async def encode(model: Any, texts: list[str]) -> list[list[float]]:
    """Run the blocking encoder in a thread so the asyncio loop isn't pinned."""
    return await asyncio.to_thread(_encode_sync, model, texts)
