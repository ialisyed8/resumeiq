"""
Email delivery.

The tests that matter here are the negative ones: what must *not* appear in a
log, and what must *not* happen in production.
"""
import pytest

from app.services.email import (
    ConsoleBackend,
    Email,
    EmailKind,
    MemoryBackend,
    password_reset_email,
    send,
    set_backend,
    verification_email,
)


@pytest.fixture
def outbox():
    backend = MemoryBackend()
    set_backend(backend)
    yield backend
    set_backend(None)


class TestDelivery:
    async def test_send_reaches_the_backend(self, outbox):
        assert await send(Email("a@example.test", "Subject", "Body"))
        assert len(outbox.outbox) == 1

    async def test_failure_returns_false_rather_than_raising(self):
        class Broken(MemoryBackend):
            async def send(self, message):
                raise RuntimeError("provider down")

        set_backend(Broken())
        # Must not raise: the caller returns an identical response whether or not
        # the address exists, and an exception would break that guarantee.
        assert await send(Email("a@example.test", "S", "B")) is False
        set_backend(None)


class TestPasswordResetEmail:
    def test_contains_a_single_use_link(self):
        message = password_reset_email("a@example.test", "tok-abc123")
        assert "tok-abc123" in message.text
        assert "reset-password?token=" in message.text

    def test_states_expiry(self):
        assert "30 minutes" in password_reset_email("a@example.test", "t").text

    def test_reassures_when_unrequested(self):
        """A reset email reaching the wrong person must not read as an alarm."""
        text = password_reset_email("a@example.test", "t").text.lower()
        assert "wasn't you" in text or "was not you" in text
        assert "has not changed" in text

    def test_kind_is_tagged_for_metrics(self):
        assert password_reset_email("a@example.test", "t").kind is EmailKind.PASSWORD_RESET


class TestVerificationEmail:
    def test_contains_link_and_expiry(self):
        message = verification_email("a@example.test", "tok-xyz")
        assert "tok-xyz" in message.text
        assert "24 hours" in message.text


class TestSecretsNeverLogged:
    async def test_send_logs_no_token_recipient_or_body(self, outbox, caplog):
        import logging

        caplog.set_level(logging.INFO)
        await send(password_reset_email("victim@example.test", "SUPER-SECRET-TOKEN"))
        logged = caplog.text
        assert "SUPER-SECRET-TOKEN" not in logged
        assert "victim@example.test" not in logged
        assert "reset-password?token=" not in logged

    async def test_failure_logs_error_type_not_detail(self, caplog):
        import logging

        class Broken(MemoryBackend):
            async def send(self, message):
                raise RuntimeError("smtp://user:hunter2@mail.example.test failed")

        set_backend(Broken())
        caplog.set_level(logging.ERROR)
        await send(password_reset_email("a@example.test", "TOKEN-VALUE"))
        assert "hunter2" not in caplog.text
        assert "TOKEN-VALUE" not in caplog.text
        set_backend(None)


class TestProductionSafety:
    async def test_console_backend_refuses_to_run_in_production(self, monkeypatch):
        """
        The console backend prints reset links to stdout. Shipping it is the same
        as publishing password resets to the log aggregator.
        """
        from app.core import config

        monkeypatch.setattr(config.settings, "APP_ENV", "production")
        with pytest.raises(RuntimeError, match="never run outside development"):
            await ConsoleBackend().send(Email("a@example.test", "S", "B"))
