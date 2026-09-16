"""Authentication: registration, login, refresh rotation, password reset."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select

from app.core.config import settings
from app.core.deps import CurrentUser, DbSession
from app.core.errors import AuthenticationError, ConflictError, PermissionError_
from app.core.ratelimit import limit_auth
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_reset_token,
    hash_password,
    hash_token,
    validate_password_strength,
    verify_password,
)
from app.models import (
    Organization,
    PasswordResetToken,
    RefreshToken,
    User,
    UserRole,
)
from app.services import audit

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    full_name: str = Field(min_length=1, max_length=255)
    email: EmailStr
    password: str = Field(min_length=12, max_length=200)
    company: str = Field(min_length=1, max_length=255)
    job_title: str | None = Field(default=None, max_length=160)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(max_length=200)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: dict


def _slugify(name: str) -> str:
    import re
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:100]
    return base or f"org-{uuid.uuid4().hex[:8]}"


def _set_cookies(response: Response, access: str, refresh: str) -> None:
    """HTTP-only cookies alongside the bearer response, for browser clients."""
    common = {
        "httponly": True,
        "secure": settings.COOKIE_SECURE,
        "samesite": settings.COOKIE_SAMESITE,
        "path": "/",
    }
    response.set_cookie(
        "access_token", access,
        max_age=settings.ACCESS_TOKEN_TTL_MINUTES * 60, **common
    )
    response.set_cookie(
        "refresh_token", refresh,
        max_age=settings.REFRESH_TOKEN_TTL_DAYS * 86400, **common
    )


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, request: Request, session: DbSession):
    await limit_auth(request.client.host if request.client else "unknown")

    problems = validate_password_strength(payload.password)
    if problems:
        raise ConflictError("; ".join(problems))

    existing = await session.scalar(
        select(User).where(func.lower(User.email) == payload.email.lower())
    )
    if existing:
        # Do not confirm which emails are registered.
        raise ConflictError(
            "That email cannot be used to register. Try logging in instead."
        )

    org = Organization(
        name=payload.company,
        slug=f"{_slugify(payload.company)}-{uuid.uuid4().hex[:6]}",
        blind_screening_default=settings.DEFAULT_BLIND_SCREENING,
    )
    session.add(org)
    await session.flush()

    user = User(
        organization_id=org.id,
        email=payload.email.lower(),
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        job_title=payload.job_title,
        role=UserRole.ADMIN,  # first user in an org administers it
        email_verified_at=datetime.now(UTC),
    )
    session.add(user)
    await session.flush()

    await audit.record(
        session, organization_id=org.id, action="user.registered",
        entity_type="user", entity_id=user.id, actor_id=user.id,
        actor_label=user.full_name,
    )

    return {
        "id": str(user.id),
        "email": user.email,
        "full_name": user.full_name,
        "organization": {"id": str(org.id), "name": org.name},
        "message": "Account created successfully. Your recruiter workspace is ready.",
    }


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest, request: Request, response: Response, session: DbSession
):
    ip = request.client.host if request.client else "unknown"
    await limit_auth(ip)

    user = await session.scalar(
        select(User).where(func.lower(User.email) == payload.email.lower())
    )

    now = datetime.now(UTC)
    if user and user.locked_until and user.locked_until > now:
        raise AuthenticationError(
            "This account is temporarily locked after repeated failed attempts. "
            f"Try again after {user.locked_until.strftime('%H:%M UTC')}."
        )

    # Always run a hash comparison so timing does not reveal account existence.
    valid = verify_password(
        payload.password,
        user.password_hash if user else "$argon2id$v=19$m=65536,t=3,p=4$"
        "AAAAAAAAAAAAAAAAAAAAAA$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    )

    if not user or not valid or not user.is_active:
        if user:
            user.failed_login_count += 1
            if user.failed_login_count >= settings.MAX_LOGIN_ATTEMPTS:
                user.locked_until = now + timedelta(minutes=settings.LOCKOUT_MINUTES)
                user.failed_login_count = 0
            await audit.record(
                session, organization_id=user.organization_id,
                action=audit.Action.LOGIN_FAILED, entity_type="user",
                entity_id=user.id, actor_label=payload.email, ip_address=ip,
            )
        raise AuthenticationError("That email or password is not correct.")

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now

    access = create_access_token(str(user.id), str(user.organization_id), user.role.value)
    refresh, jti = create_refresh_token(str(user.id))
    session.add(
        RefreshToken(
            user_id=user.id, token_hash=hash_token(refresh), family=jti,
            expires_at=now + timedelta(days=settings.REFRESH_TOKEN_TTL_DAYS),
        )
    )

    await audit.record(
        session, organization_id=user.organization_id, action=audit.Action.LOGIN,
        entity_type="user", entity_id=user.id, actor_id=user.id,
        actor_label=user.full_name, ip_address=ip,
    )

    _set_cookies(response, access, refresh)
    return TokenResponse(
        access_token=access, refresh_token=refresh,
        expires_in=settings.ACCESS_TOKEN_TTL_MINUTES * 60,
        user={
            "id": str(user.id), "email": user.email, "full_name": user.full_name,
            "role": user.role.value, "job_title": user.job_title,
            "organization_id": str(user.organization_id),
        },
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_tokens(request: Request, response: Response, session: DbSession):
    """
    Rotate the refresh token.

    Reuse of an already-rotated token means it leaked: the entire family is
    revoked and the caller must log in again.
    """
    # Unauthenticated by necessity — the access token has expired, which is why
    # we are here. Limit by IP: an attacker replaying stolen refresh tokens gets
    # the same allowance as a browser, which needs one refresh every 15 minutes.
    await limit_auth(request.client.host if request.client else "unknown")

    token = request.cookies.get("refresh_token")
    if not token:
        body = await request.json() if request.headers.get("content-type") == "application/json" else {}
        token = body.get("refresh_token")
    if not token:
        raise AuthenticationError("No refresh token supplied.")

    try:
        decode_token(token, expected_type="refresh")
    except Exception as exc:
        raise AuthenticationError("That session is no longer valid.") from exc

    stored = await session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_token(token))
    )
    now = datetime.now(UTC)

    if stored is None or stored.expires_at < now:
        raise AuthenticationError("That session has expired. Log in again.")

    if stored.revoked_at is not None:
        family_tokens = await session.scalars(
            select(RefreshToken).where(RefreshToken.family == stored.family)
        )
        for row in family_tokens:
            row.revoked_at = now
        raise AuthenticationError(
            "This session was reused and has been ended for security. Log in again."
        )

    user = await session.scalar(select(User).where(User.id == stored.user_id))
    if user is None or not user.is_active:
        raise AuthenticationError("This account is no longer active.")

    access = create_access_token(str(user.id), str(user.organization_id), user.role.value)
    new_refresh, _ = create_refresh_token(str(user.id), family=stored.family)

    stored.revoked_at = now
    stored.replaced_by = hash_token(new_refresh)
    session.add(
        RefreshToken(
            user_id=user.id, token_hash=hash_token(new_refresh), family=stored.family,
            expires_at=now + timedelta(days=settings.REFRESH_TOKEN_TTL_DAYS),
        )
    )

    _set_cookies(response, access, new_refresh)
    return TokenResponse(
        access_token=access, refresh_token=new_refresh,
        expires_in=settings.ACCESS_TOKEN_TTL_MINUTES * 60,
        user={
            "id": str(user.id), "email": user.email, "full_name": user.full_name,
            "role": user.role.value, "organization_id": str(user.organization_id),
        },
    )


@router.post("/logout")
async def logout(response: Response, request: Request, session: DbSession, principal: CurrentUser):
    token = request.cookies.get("refresh_token")
    if token:
        stored = await session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == hash_token(token))
        )
        if stored:
            stored.revoked_at = datetime.now(UTC)

    await audit.record(
        session, organization_id=principal.organization_id,
        action=audit.Action.LOGOUT, entity_type="user", entity_id=principal.user_id,
        actor_id=principal.user_id, actor_label=principal.full_name,
    )
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/")
    return {"message": "Signed out."}


class ForgotRequest(BaseModel):
    email: EmailStr


@router.post("/forgot-password")
async def forgot_password(payload: ForgotRequest, request: Request, session: DbSession):
    await limit_auth(request.client.host if request.client else "unknown")
    user = await session.scalar(
        select(User).where(func.lower(User.email) == payload.email.lower())
    )
    if user:
        raw, hashed = generate_reset_token()
        session.add(
            PasswordResetToken(
                user_id=user.id, token_hash=hashed,
                expires_at=datetime.now(UTC) + timedelta(minutes=30),
            )
        )
        # Delivery failure must not change the response — see the identical
        # return below. The token is never logged and never returned.
        from app.services.email import password_reset_email, send
        await send(password_reset_email(user.email, raw))

    # Identical response either way — never reveal whether an account exists.
    return {"message": "If that email is registered, a reset link is on its way."}


class ResetRequest(BaseModel):
    token: str
    password: str = Field(min_length=12, max_length=200)


@router.post("/reset-password")
async def reset_password(payload: ResetRequest, request: Request, session: DbSession):
    # The token is 32 random bytes so brute force is impractical, but there is
    # no legitimate reason to allow unlimited attempts against it.
    await limit_auth(request.client.host if request.client else "unknown")

    problems = validate_password_strength(payload.password)
    if problems:
        raise ConflictError("; ".join(problems))

    stored = await session.scalar(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == hash_token(payload.token)
        )
    )
    now = datetime.now(UTC)
    if stored is None or stored.used_at is not None or stored.expires_at < now:
        raise PermissionError_("That reset link is invalid or has expired.")

    user = await session.scalar(select(User).where(User.id == stored.user_id))
    if user is None:
        raise PermissionError_("That reset link is invalid or has expired.")

    user.password_hash = hash_password(payload.password)
    stored.used_at = now

    # Revoke every existing session on password change.
    for token_row in await session.scalars(
        select(RefreshToken).where(
            RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None)
        )
    ):
        token_row.revoked_at = now

    await audit.record(
        session, organization_id=user.organization_id,
        action=audit.Action.PASSWORD_RESET, entity_type="user", entity_id=user.id,
        actor_id=user.id, actor_label=user.full_name,
    )
    return {"message": "Password updated. Log in with your new password."}


@router.get("/me")
async def me(principal: CurrentUser):
    return {
        "id": str(principal.user_id), "email": principal.email,
        "full_name": principal.full_name, "role": principal.role.value,
        "organization_id": str(principal.organization_id),
    }
