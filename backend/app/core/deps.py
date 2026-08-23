"""Shared FastAPI dependencies: auth, tenancy, rate limiting."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AuthenticationError, PermissionError_, RateLimitError
from app.core.security import TokenError, decode_token
from app.db.session import get_session
from app.models import User, UserRole


@dataclass(slots=True)
class Principal:
    """The authenticated caller. Every query is scoped by organization_id."""

    user_id: uuid.UUID
    organization_id: uuid.UUID
    role: UserRole
    email: str
    full_name: str

    @property
    def is_admin(self) -> bool:
        return self.role is UserRole.ADMIN


def _bearer(request: Request, authorization: str | None) -> str:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    cookie = request.cookies.get("access_token")
    if cookie:
        return cookie
    raise AuthenticationError("Log in to continue.")


async def current_user(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    token = _bearer(request, authorization)
    try:
        payload = decode_token(token, expected_type="access")
    except TokenError as exc:
        raise AuthenticationError(str(exc)) from exc

    user = await session.scalar(
        select(User).where(User.id == uuid.UUID(payload["sub"]))
    )
    if user is None or not user.is_active:
        raise AuthenticationError("This account is no longer active.")

    return Principal(
        user_id=user.id, organization_id=user.organization_id, role=user.role,
        email=user.email, full_name=user.full_name,
    )


async def require_admin(
    principal: Annotated[Principal, Depends(current_user)]
) -> Principal:
    if not principal.is_admin:
        raise PermissionError_("This action requires an administrator account.")
    return principal


CurrentUser = Annotated[Principal, Depends(current_user)]
AdminUser = Annotated[Principal, Depends(require_admin)]
DbSession = Annotated[AsyncSession, Depends(get_session)]

# ---------------------------------------------------------------------------
# Rate-limited principals
#
# Applied as a dependency rather than a call inside each handler, so a new
# endpoint cannot silently ship without a limit — the type annotation is the
# enforcement. Keyed on user id, so opening a second tab does not double the
# allowance.
# ---------------------------------------------------------------------------

def _tiered(tier):
    async def dependency(
        principal: Annotated[Principal, Depends(current_user)]
    ) -> Principal:
        from app.core.limits import enforce_rate_limit
        await enforce_rate_limit(tier, str(principal.user_id))
        return principal
    return dependency


def _read_dep():
    from app.core.limits import Tier
    return _tiered(Tier.READ)


def _write_dep():
    from app.core.limits import Tier
    return _tiered(Tier.WRITE)


def _upload_dep():
    from app.core.limits import Tier
    return _tiered(Tier.UPLOAD)


def _ai_dep():
    from app.core.limits import Tier
    return _tiered(Tier.AI)


ReadUser = Annotated[Principal, Depends(_read_dep())]
WriteUser = Annotated[Principal, Depends(_write_dep())]
UploadUser = Annotated[Principal, Depends(_upload_dep())]
AiUser = Annotated[Principal, Depends(_ai_dep())]
