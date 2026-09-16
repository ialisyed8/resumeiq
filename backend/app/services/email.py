"""
Transactional email.

Provider-agnostic by design: the provider is a config value, not an import.
Swapping SES for Postmark is an env change, and tests use the in-memory backend
rather than mocking a vendor SDK.

The security rules here are absolute:

* **Never log a token, or a URL containing one.** A reset link in a log file is
  a password reset anyone with log access can perform.
* **Never log the recipient address.** It is candidate or customer PII.
* **Never reveal whether an address is registered.** The caller sends the same
  response either way; this module must not undermine that by failing loudly for
  unknown addresses.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from enum import Enum

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class EmailKind(str, Enum):
    PASSWORD_RESET = "password_reset"
    EMAIL_VERIFICATION = "email_verification"
    SCREENING_COMPLETE = "screening_complete"


@dataclass(slots=True)
class Email:
    to: str
    subject: str
    text: str
    html: str | None = None
    kind: EmailKind = EmailKind.PASSWORD_RESET


class EmailBackend(abc.ABC):
    @abc.abstractmethod
    async def send(self, message: Email) -> None: ...


class ConsoleBackend(EmailBackend):
    """
    Development backend.

    Prints the body to stdout, which does include the reset link — acceptable
    only because it refuses to run outside development. In any other environment
    this raises rather than quietly printing secrets to a log aggregator.
    """

    async def send(self, message: Email) -> None:
        if settings.APP_ENV not in ("development", "test"):
            raise RuntimeError(
                "ConsoleBackend prints reset links to stdout and must never run "
                "outside development. Configure EMAIL_BACKEND."
            )
        print(f"\n--- EMAIL [{message.kind.value}] ---\nTo: {message.to}\n"
              f"Subject: {message.subject}\n\n{message.text}\n--- END ---\n")


@dataclass(slots=True)
class MemoryBackend(EmailBackend):
    """Test backend. Lets a test assert an email was sent without inspecting logs."""

    outbox: list[Email] = field(default_factory=list)

    async def send(self, message: Email) -> None:
        self.outbox.append(message)

    def last_for(self, to: str) -> Email | None:
        return next((m for m in reversed(self.outbox) if m.to == to), None)

    def clear(self) -> None:
        self.outbox.clear()


class SMTPBackend(EmailBackend):
    """Generic SMTP — works with SES, Postmark, Mailgun, or a local relay."""

    async def send(self, message: Email) -> None:
        import asyncio
        from email.message import EmailMessage

        def _send() -> None:
            import smtplib

            msg = EmailMessage()
            msg["Subject"] = message.subject
            msg["From"] = settings.EMAIL_FROM
            msg["To"] = message.to
            msg.set_content(message.text)
            if message.html:
                msg.add_alternative(message.html, subtype="html")

            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=20) as smtp:
                if settings.SMTP_USE_TLS:
                    smtp.starttls()
                if settings.SMTP_USERNAME:
                    smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
                smtp.send_message(msg)

        await asyncio.to_thread(_send)


_backend: EmailBackend | None = None


def get_backend() -> EmailBackend:
    global _backend
    if _backend is None:
        choice = (settings.EMAIL_BACKEND or "console").lower()
        _backend = {
            "console": ConsoleBackend, "memory": MemoryBackend, "smtp": SMTPBackend,
        }.get(choice, ConsoleBackend)()
    return _backend


def set_backend(backend: EmailBackend | None) -> None:
    """Test hook."""
    global _backend
    _backend = backend


async def send(message: Email) -> bool:
    """
    Deliver a message.

    Returns success rather than raising, because a failure to send a reset email
    must not change the API response — that response is identical whether or not
    the address exists, and an exception here would break that guarantee.

    Note what is logged: the kind, and nothing else. No recipient, no subject,
    no body, no token.
    """
    try:
        await get_backend().send(message)
        logger.info("email_sent", kind=message.kind.value)
        return True
    except Exception as exc:
        logger.error("email_failed", kind=message.kind.value, error=type(exc).__name__)
        return False


def password_reset_email(to: str, token: str) -> Email:
    link = f"{settings.APP_BASE_URL.rstrip('/')}/reset-password?token={token}"
    return Email(
        to=to,
        subject="Reset your ResumeIQ password",
        kind=EmailKind.PASSWORD_RESET,
        text=(
            "Someone asked to reset the password for this ResumeIQ account.\n\n"
            f"{link}\n\n"
            "This link works once and expires in 30 minutes.\n\n"
            "If this wasn't you, no action is needed — your password has not "
            "changed and this link will expire on its own."
        ),
    )


def verification_email(to: str, token: str) -> Email:
    link = f"{settings.APP_BASE_URL.rstrip('/')}/verify-email?token={token}"
    return Email(
        to=to,
        subject="Confirm your ResumeIQ email address",
        kind=EmailKind.EMAIL_VERIFICATION,
        text=(
            "Confirm this address to finish setting up your ResumeIQ account.\n\n"
            f"{link}\n\n"
            "This link expires in 24 hours."
        ),
    )
