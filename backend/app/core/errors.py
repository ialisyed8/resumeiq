"""
Application errors and the exception handlers that render them.

Users see an actionable message. Stack traces, SQL, and model output go to the
logs with a request id attached so support can correlate the two.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)


class AppError(Exception):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "app_error"
    message = "Something went wrong."

    def __init__(self, message: str | None = None, *, detail: dict | None = None):
        self.message = message or self.message
        self.detail = detail or {}
        super().__init__(self.message)


class AuthenticationError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "authentication_failed"
    message = "Your session has expired. Log in again to continue."


class PermissionError_(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "permission_denied"
    message = "You do not have access to this."


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"
    message = "That record does not exist."


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"


class RateLimitError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"
    message = "Too many requests. Wait a moment and try again."


class ConfigurationError(AppError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "configuration_error"
    message = "The server is not configured correctly. Contact your administrator."


class UploadError(AppError):
    code = "upload_rejected"
    message = "That file could not be accepted."


class ExtractionError(AppError):
    code = "extraction_failed"
    message = "The document could not be read reliably."


class ModelError(AppError):
    status_code = status.HTTP_502_BAD_GATEWAY
    code = "model_error"
    message = "The analysis service is unavailable. The batch will retry automatically."


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        logger.warning(
            "app_error", extra={"code": exc.code, "request_id": request_id,
                                "path": request.url.path, "detail": exc.detail})
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message,
                               "request_id": request_id, **({"detail": exc.detail} if exc.detail else {})}},
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"error": {"code": "validation_error",
                               "message": "Some fields need attention.",
                               "request_id": request_id,
                               "fields": [{"field": ".".join(str(p) for p in e["loc"][1:]),
                                           "message": e["msg"]} for e in exc.errors()]}},
        )

    @app.exception_handler(SQLAlchemyError)
    async def _db(request: Request, exc: SQLAlchemyError):
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        logger.exception("database_error", extra={"request_id": request_id})
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"error": {"code": "database_unavailable",
                               "message": "The service is temporarily unavailable. Try again shortly.",
                               "request_id": request_id}},
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        logger.exception("unhandled_error", extra={"request_id": request_id,
                                                   "path": request.url.path})
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": {"code": "internal_error",
                               "message": "Something went wrong on our side. The team has been notified.",
                               "request_id": request_id}},
        )
