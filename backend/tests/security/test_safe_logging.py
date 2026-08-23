"""
Logging must not become a second, less-protected copy of the candidate database.

Logs are retained longer, replicated more widely, and access-controlled more
loosely than Postgres. Anything logged is effectively published inside the
company. This suite is a static check over the source: it fails the build if a
future change starts logging candidate content or credentials.
"""
import pathlib
import re

import pytest

APP = pathlib.Path(__file__).resolve().parents[2] / "app"

#: Names that carry candidate content or secrets. Logging any of these directly
#: is a policy violation regardless of intent.
FORBIDDEN_IN_LOGS = [
    "extracted_text", "resume_text", "quote_preview", "full_text",
    "password", "token_hash", "refresh_token", "access_token",
    "api_key", "secret", "authorization",
    "candidate_name", "full_name", "email_address",
]

#: Structural patterns that leak content even without a giveaway name.
SLICING_PATTERNS = [
    re.compile(r'"[a-z_]*(?:quote|text|content|body)[a-z_]*"\s*:\s*\w+\[:\d+\]'),
    re.compile(r'\b(?:quote|text|content)\s*=\s*\w+\[:\d+\]'),
]

LOG_CALL = re.compile(
    r"(?:logger|log)\.(?:debug|info|warning|error|critical|exception)\s*\(",
)


def _log_call_bodies(source: str):
    """Yield the argument text of each logging call."""
    for match in LOG_CALL.finditer(source):
        depth, i = 0, match.end() - 1
        while i < len(source):
            if source[i] == "(":
                depth += 1
            elif source[i] == ")":
                depth -= 1
                if depth == 0:
                    yield source[match.end():i]
                    break
            i += 1


def _modules():
    return [p for p in APP.rglob("*.py") if "__pycache__" not in str(p)]


class TestNoSensitiveDataInLogs:
    @pytest.mark.parametrize("forbidden", FORBIDDEN_IN_LOGS)
    def test_forbidden_field_never_logged(self, forbidden):
        offenders = []
        for module in _modules():
            for body in _log_call_bodies(module.read_text()):
                if forbidden in body:
                    offenders.append(f"{module.name}: {body.strip()[:100]}")
        assert not offenders, (
            f"'{forbidden}' appears in a log call:\n  " + "\n  ".join(offenders)
        )

    def test_no_sliced_document_content_logged(self):
        """`quote[:80]` is still 80 characters of someone's resume."""
        offenders = []
        for module in _modules():
            for body in _log_call_bodies(module.read_text()):
                for pattern in SLICING_PATTERNS:
                    if pattern.search(body):
                        offenders.append(f"{module.name}: {body.strip()[:100]}")
        assert not offenders, "document content sliced into a log:\n  " + "\n  ".join(offenders)

    def test_validation_logs_metadata_not_content(self):
        """
        The ungrounded-quote path is the one place most tempted to log the text.
        It must log shape, not substance.
        """
        source = (APP / "ai" / "validation.py").read_text()
        bodies = list(_log_call_bodies(source))
        joined = " ".join(bodies)
        assert "quote_preview" not in joined
        assert "quote_length" in joined or "similarity" in joined


class TestNoCredentialValuesLogged:
    """
    Distinct from the name check above: `note="set ANTHROPIC_API_KEY"` is a
    helpful message, while `key=settings.ANTHROPIC_API_KEY` publishes the
    credential. Only the second is a violation.
    """

    CREDENTIAL_EXPRESSIONS = [
        "settings.ANTHROPIC_API_KEY", "settings.SECRET_KEY",
        "settings.JWT_SECRET", "settings.S3_SECRET_KEY",
        "settings.DATABASE_URL", "settings.REDIS_URL",
    ]

    def test_credential_values_never_logged(self):
        offenders = []
        for module in _modules():
            for body in _log_call_bodies(module.read_text()):
                for expression in self.CREDENTIAL_EXPRESSIONS:
                    if expression in body:
                        offenders.append(f"{module.name}: {expression}")
        assert not offenders, f"credential value logged: {offenders}"

    def test_naming_a_setting_in_guidance_is_allowed(self):
        """Guard against over-tightening: this message is good and must survive."""
        source = (APP / "main.py").read_text()
        assert "ANTHROPIC_API_KEY" in source


class TestNoSecretsInSource:
    def test_no_hardcoded_provider_keys(self):
        pattern = re.compile(r"sk-ant-[A-Za-z0-9]{10,}")
        offenders = [m.name for m in _modules() if pattern.search(m.read_text())]
        assert not offenders, f"provider key literal in: {offenders}"

    def test_models_referenced_only_through_config(self):
        offenders = [
            m.name for m in _modules()
            if "claude-" in m.read_text() and m.name != "config.py"
        ]
        assert not offenders, f"hardcoded model name in: {offenders}"
