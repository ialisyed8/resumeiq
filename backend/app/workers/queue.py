"""Redis queue via arq."""

from __future__ import annotations

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)
_pool = None


async def get_pool():
    global _pool
    if _pool is None:
        from arq import create_pool
        from arq.connections import RedisSettings

        _pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
    return _pool


async def enqueue_screening(batch_id: str) -> None:
    pool = await get_pool()
    await pool.enqueue_job("run_screening_task", batch_id)
    logger.info("screening_enqueued", batch=batch_id)


async def enqueue_reprocess(document_id: str, force_ocr: bool = False) -> None:
    pool = await get_pool()
    await pool.enqueue_job("reprocess_document_task", document_id, force_ocr)
