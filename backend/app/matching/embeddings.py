"""
Embedding generation and pgvector retrieval.

The rule this module exists to enforce: embeddings retrieve, they never judge.
Cosine similarity answers "which passages might discuss Kubernetes?" — it does
not answer "does this candidate know Kubernetes?". No similarity number ever
reaches a score.
"""

from __future__ import annotations

import asyncio
from functools import lru_cache

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@lru_cache(maxsize=1)
def _model():
    """Load lazily — the model is ~440MB and workers need it, the API does not."""
    from sentence_transformers import SentenceTransformer

    logger.info("loading_embedding_model", model=settings.EMBEDDING_MODEL)
    return SentenceTransformer(settings.EMBEDDING_MODEL)


def _encode(texts: list[str], *, is_query: bool) -> list[list[float]]:
    if not texts:
        return []
    model = _model()
    # BGE models expect an instruction prefix on the query side only. Read the
    # model card before swapping EMBEDDING_MODEL — this differs per family.
    if is_query and "bge" in settings.EMBEDDING_MODEL.lower():
        texts = [settings.EMBEDDING_QUERY_PREFIX + t for t in texts]
    vectors = model.encode(
        texts, batch_size=settings.EMBEDDING_BATCH_SIZE,
        normalize_embeddings=True, show_progress_bar=False,
    )
    return [v.tolist() for v in vectors]


async def embed_passages(texts: list[str]) -> list[list[float]]:
    """Embed resume chunks. Runs in a thread — sentence-transformers is sync."""
    return await asyncio.to_thread(_encode, texts, is_query=False)


async def embed_queries(texts: list[str]) -> list[list[float]]:
    """Embed requirement texts for retrieval."""
    return await asyncio.to_thread(_encode, texts, is_query=True)


def cosine(a: list[float], b: list[float]) -> float:
    """Both vectors are normalised at encode time, so this is a dot product."""
    return sum(x * y for x, y in zip(a, b))
