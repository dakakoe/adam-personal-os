from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)

# Must match what embedding_worker uses on the writer side. If you bump
# the worker's model, you must rebuild memory.interaction_embedding from
# scratch and then bump this constant — different models live in
# incompatible vector spaces.
DEFAULT_MODEL = "intfloat/multilingual-e5-base"
DEFAULT_CACHE = "/srv/memory/data/embedding-models"

_model: Any = None
_lock = asyncio.Lock()


async def get_model() -> Any:
    """Lazy single-instance loader. The MCP server starts in <1s; this
    pushes the ~3-5s torch + model load to the first semantic_search call
    rather than every cold start. Subsequent calls are cached."""
    global _model
    if _model is not None:
        return _model
    async with _lock:
        if _model is None:
            import torch
            from sentence_transformers import SentenceTransformer

            torch.set_num_threads(1)  # MCP server is interactive, not bulk
            log.info("loading embedding model %s", DEFAULT_MODEL)
            _model = SentenceTransformer(
                DEFAULT_MODEL,
                device="cpu",
                cache_folder=DEFAULT_CACHE,
            )
    return _model


def _encode_sync(model: Any, query: str) -> list[float]:
    """E5 family expects a 'query: ' prefix on the search side
    (mirroring the embedder's 'passage: ' prefix on the document side).
    normalize_embeddings=True so cosine == dot product."""
    arr = model.encode(
        [f"query: {query}"],
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    return arr[0].tolist()


async def encode_query(query: str) -> list[float]:
    model = await get_model()
    return await asyncio.to_thread(_encode_sync, model, query)
