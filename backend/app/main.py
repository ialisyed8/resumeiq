"""
FastAPI application.

Security headers, CORS, request correlation, and body-size limits are applied
here as middleware so every route inherits them.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.router import api_router
from app.core.config import settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging, get_logger, new_request_id, request_id_var

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    logger.info(
        "starting", env=settings.APP_ENV, ai_enabled=settings.ai_enabled,
        scorer_version=__import__("app.scoring.engine", fromlist=["SCORER_VERSION"]).SCORER_VERSION,
    )
    if not settings.ai_enabled:
        logger.warning(
            "anthropic_not_configured",
            note="Running without ANTHROPIC_API_KEY. Requirement extraction and "
                 "evidence verification are unavailable; seeded data still works.",
        )
    try:
        from app.services.storage import storage
        storage.ensure_bucket()
    except Exception as exc:
        logger.warning("storage_unavailable", error=str(exc)[:200])

    yield

    from app.db.session import dispose_engine
    await dispose_engine()


app = FastAPI(
    title="ResumeIQ API",
    version="1.0.0",
    description=(
        "Requirement-first resume screening. Candidates are ranked by evidence "
        "against recruiter-approved requirements; the recruiter decides."
    ),
    docs_url="/api/docs" if settings.APP_ENV != "production" else None,
    redoc_url=None,
    openapi_url="/api/openapi.json" if settings.APP_ENV != "production" else None,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,  # never "*" in production; enforced in config
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    max_age=600,
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or new_request_id()
    request_id_var.set(request_id)
    request.state.request_id = request_id

    length = request.headers.get("content-length")
    if length and int(length) > settings.MAX_REQUEST_BYTES:
        return JSONResponse(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            content={"error": {"code": "payload_too_large",
                               "message": "That upload is too large.",
                               "request_id": request_id}},
        )

    started = time.perf_counter()
    response = await call_next(request)
    duration_ms = round((time.perf_counter() - started) * 1000, 2)

    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    if settings.APP_ENV == "production":
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains; preload"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' "
            "https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; "
            "base-uri 'self'; form-action 'self'"
        )

    if not request.url.path.endswith("/events"):
        logger.info(
            "request", method=request.method, path=request.url.path,
            status=response.status_code, duration_ms=duration_ms,
        )
    return response


register_error_handlers(app)
app.include_router(api_router, prefix=settings.API_V1_PREFIX)


@app.get("/api/health")
async def health():
    return {"status": "ok", "env": settings.APP_ENV, "ai_configured": settings.ai_enabled}


@app.get("/api/health/deep")
async def health_deep():
    """Dependency check for readiness probes."""
    from sqlalchemy import text

    from app.ai.client import get_client
    from app.db.session import SessionFactory

    checks: dict = {}
    try:
        async with SessionFactory() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = {"ok": True}
    except Exception as exc:
        checks["database"] = {"ok": False, "detail": str(exc)[:160]}

    try:
        from app.workers.queue import get_pool
        pool = await get_pool()
        await pool.ping()
        checks["redis"] = {"ok": True}
    except Exception as exc:
        checks["redis"] = {"ok": False, "detail": str(exc)[:160]}

    try:
        from app.services.storage import storage
        storage._s3().head_bucket(Bucket=settings.S3_BUCKET)
        checks["storage"] = {"ok": True}
    except Exception as exc:
        checks["storage"] = {"ok": False, "detail": str(exc)[:160]}

    from app.services import malware
    checks["malware_scanner"] = await malware.health()

    checks["anthropic"] = await get_client().health()
    healthy = all(c.get("ok", True) for c in checks.values())
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "ok" if healthy else "degraded", "checks": checks},
    )
