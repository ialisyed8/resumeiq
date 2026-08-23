"""
arq worker entry point.

Run with:  arq app.workers.main.WorkerSettings
"""

from __future__ import annotations

from arq import cron
from arq.connections import RedisSettings

from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.workers.pipeline import run_screening

logger = get_logger(__name__)


async def run_screening_task(ctx, batch_id: str) -> dict:
    return await run_screening(batch_id)


async def reprocess_document_task(ctx, document_id: str, force_ocr: bool = False) -> dict:
    """Retry a quarantined document, optionally forcing the OCR path."""
    import uuid

    from sqlalchemy import select

    from app.db.session import SessionFactory
    from app.models import DocumentStatus, ResumeDocument

    async with SessionFactory() as session:
        document = await session.scalar(
            select(ResumeDocument).where(ResumeDocument.id == uuid.UUID(document_id))
        )
        if document is None:
            return {"status": "missing"}
        document.status = DocumentStatus.PENDING
        if force_ocr:
            document.quality_signals = {
                **(document.quality_signals or {}), "has_text_layer": False
            }
        await session.commit()
    return {"status": "requeued", "document_id": document_id}


async def startup(ctx) -> None:
    configure_logging()
    logger.info(
        "worker_started",
        ai_enabled=settings.ai_enabled,
        extraction_model=settings.ANTHROPIC_EXTRACTION_MODEL,
        embedding_model=settings.EMBEDDING_MODEL,
    )
    if not settings.ai_enabled:
        logger.warning(
            "anthropic_not_configured",
            note="Requirement extraction and evidence verification are disabled. "
                 "Set ANTHROPIC_API_KEY to enable them.",
        )


async def shutdown(ctx) -> None:
    from app.db.session import dispose_engine
    await dispose_engine()


async def retention_task(ctx) -> dict:
    """Nightly data-lifecycle enforcement."""
    from app.db.session import SessionFactory
    from app.services.retention import run_retention

    async with SessionFactory() as session:
        report = await run_retention(session)
    return report.as_dict()


class WorkerSettings:
    functions = [run_screening_task, reprocess_document_task, retention_task]
    # Runs at 03:15 UTC daily. Deliberately not midnight: that is when every
    # other scheduled job in every other system also runs.
    cron_jobs = [cron(retention_task, hour=3, minute=15)]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    max_jobs = 4
    job_timeout = 3600
    keep_result = 3600
    max_tries = 3
