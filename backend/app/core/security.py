"""Password hashing, JWTs, and account lockout."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from jose import JWTError, jwt

from app.core.config import settings

_hasher = PasswordHasher(
    time_cost=settings.ARGON2_TIME_COST,
    memory_cost=settings.ARGON2_MEMORY_COST,
    parallelism=settings.ARGON2_PARALLELISM,
)

TokenType = Literal["access", "refresh"]


class TokenError(Exception):
    """Raised for any invalid, expired, or wrong-type token."""


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        _hasher.verify(stored_hash, password)
        return True
    except (VerifyMismatchError, InvalidHashError, Exception):
        return False


def needs_rehash(stored_hash: str) -> bool:
    """True when Argon2 parameters have been raised since this hash was made."""
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except Exception:
        return True


def validate_password_strength(password: str) -> list[str]:
    """Return a list of problems; empty means acceptable."""
    problems = []
    if len(password) < settings.PASSWORD_MIN_LENGTH:
        problems.append(
            f"Password must be at least {settings.PASSWORD_MIN_LENGTH} characters"
        )
    if password.lower() in {"password", "changeme", "letmein"} or password.isdigit():
        problems.append("Password is too common")
    return problems


def _create_token(subject: str, token_type: TokenType, ttl: timedelta, **claims) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "iat": now,
        "exp": now + ttl,
        "jti": secrets.token_urlsafe(16),
        **claims,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_access_token(user_id: str, org_id: str, role: str) -> str:
    return _create_token(
        user_id,
        "access",
        timedelta(minutes=settings.ACCESS_TOKEN_TTL_MINUTES),
        org=org_id,
        role=role,
    )


def create_refresh_token(user_id: str, family: str | None = None) -> tuple[str, str]:
    """
    Returns (token, jti). The jti is stored so the token can be revoked and so
    reuse of a rotated token can be detected — reuse invalidates the whole family.
    """
    jti = secrets.token_urlsafe(16)
    token = _create_token(
        user_id,
        "refresh",
        timedelta(days=settings.REFRESH_TOKEN_TTL_DAYS),
        family=family or jti,
    )
    return token, jti


def decode_token(token: str, expected_type: TokenType | None = None) -> dict:
    try:
        payload = jwt.decode(
            token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
        )
    except JWTError as exc:
        raise TokenError("Token is invalid or expired") from exc

    if expected_type and payload.get("type") != expected_type:
        raise TokenError(f"Expected a {expected_type} token")
    return payload


def hash_token(token: str) -> str:
    """Refresh tokens are stored hashed, like passwords."""
    return hashlib.sha256(token.encode()).hexdigest()


def constant_time_compare(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def generate_reset_token() -> tuple[str, str]:
    """Returns (plaintext for the email link, hash for the database)."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)
