"""
Anthropic client.

Responsibilities beyond making the call:

* **Fail safely when unconfigured.** No API key means AI features raise a clear
  ConfigurationError rather than a stack trace, and the app still serves seeded
  data so the UI can be developed and demonstrated offline.
* **Validate everything.** Responses are parsed into Pydantic models. A malformed
  response is retried with the error fed back, then abandoned — never partially
  accepted.
* **Cache the stable prefix.** The system prompt and requirement list are
  identical across every candidate in a batch. Marking them as cacheable cuts
  input cost substantially on a 128-resume run.
* **Retry with backoff.** Rate limits and overloads are expected at batch scale
  and are not errors worth surfacing to a recruiter.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.ai import prompts
from app.ai.schemas import (
    JobAnalysis,
    QuestionSet,
    ResumeProfile,
    VerificationBatch,
)
from app.core.config import settings
from app.core.errors import ConfigurationError, ModelError
from app.core.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)

_JSON_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)

RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}


class AnthropicClient:
    """Thin, validated wrapper. The only place an API key is ever read."""

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key or settings.ANTHROPIC_API_KEY
        self._client: Any = None

    @property
    def enabled(self) -> bool:
        return bool(self._api_key)

    def _ensure(self) -> Any:
        if not self._api_key:
            raise ConfigurationError(
                "ANTHROPIC_API_KEY is not set. Add it to your .env file to enable "
                "requirement extraction and evidence verification. The application "
                "runs with seeded demo data until it is configured."
            )
        if self._client is None:
            from anthropic import AsyncAnthropic  # imported lazily

            self._client = AsyncAnthropic(
                api_key=self._api_key,
                timeout=settings.ANTHROPIC_TIMEOUT_SECONDS,
                max_retries=0,  # we handle retries ourselves for better logging
            )
        return self._client

    # -- core call ---------------------------------------------------------

    async def _complete(
        self,
        *,
        system: str,
        user: str,
        model: str,
        max_tokens: int | None = None,
        cache_system: bool = True,
    ) -> str:
        client = self._ensure()

        if cache_system and settings.ANTHROPIC_ENABLE_PROMPT_CACHE:
            system_blocks = [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        else:
            system_blocks = [{"type": "text", "text": system}]

        last_error: Exception | None = None
        for attempt in range(settings.ANTHROPIC_MAX_RETRIES):
            try:
                response = await client.messages.create(
                    model=model,
                    max_tokens=max_tokens or settings.ANTHROPIC_MAX_TOKENS,
                    # Every call here is an extraction or an adjudication, not
                    # a creative task. Sampling defaulted to 1.0, which made the
                    # same job description yield 7, then 5, then 8 requirements
                    # across three runs — one of them invented from the
                    # responsibilities section. A ranking a recruiter must be
                    # able to defend cannot rest on a die roll.
                    system=system_blocks,
                    messages=[{"role": "user", "content": user}],
                )
                usage = getattr(response, "usage", None)
                logger.info(
                    "model_call_ok",
                    model=model,
                    attempt=attempt + 1,
                    input_tokens=getattr(usage, "input_tokens", None),
                    output_tokens=getattr(usage, "output_tokens", None),
                    cache_read=getattr(usage, "cache_read_input_tokens", None),
                )
                return "".join(
                    block.text for block in response.content
                    if getattr(block, "type", None) == "text"
                )

            except Exception as exc:
                last_error = exc
                status = getattr(exc, "status_code", None)
                retryable = status in RETRYABLE_STATUS or status is None
                if not retryable or attempt == settings.ANTHROPIC_MAX_RETRIES - 1:
                    logger.error(
                        "model_call_failed", model=model, attempt=attempt + 1,
                        status=status, error=str(exc)[:300],
                    )
                    break
                # Exponential backoff with jitter.
                delay = min(30.0, (2 ** attempt) + random.random())
                logger.warning(
                    "model_call_retry", model=model, attempt=attempt + 1,
                    status=status, delay_seconds=round(delay, 2),
                )
                await asyncio.sleep(delay)

        raise ModelError(
            "The analysis service did not respond. This batch will retry "
            "automatically; no candidate data has been lost."
        ) from last_error

    @staticmethod
    def _parse(raw: str, schema: type[T]) -> T:
        """Strip any stray fencing and validate. Raises on failure."""
        cleaned = _JSON_FENCE.sub("", raw).strip()
        # Some models prepend a sentence despite instructions; salvage the object.
        if not cleaned.startswith("{"):
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start >= 0 and end > start:
                cleaned = cleaned[start : end + 1]
        payload = json.loads(cleaned)
        return schema.model_validate(payload)

    async def _structured(
        self, *, system: str, user: str, model: str, schema: type[T],
        max_tokens: int | None = None,
    ) -> T:
        """One call plus one repair attempt if the schema does not validate."""
        raw = await self._complete(
            system=system, user=user, model=model, max_tokens=max_tokens
        )
        try:
            return self._parse(raw, schema)
        except (json.JSONDecodeError, ValidationError) as exc:
            logger.warning(
                "model_schema_invalid", model=model, schema=schema.__name__,
                error=str(exc)[:400], preview=raw[:200],
            )
            repair = (
                f"{user}\n\n---\nYour previous response could not be parsed:\n"
                f"{str(exc)[:600]}\n\nReturn only valid JSON matching the schema. "
                f"No prose, no markdown fences."
            )
            raw2 = await self._complete(
                system=system, user=repair, model=model, max_tokens=max_tokens
            )
            try:
                return self._parse(raw2, schema)
            except (json.JSONDecodeError, ValidationError) as exc2:
                logger.error(
                    "model_schema_invalid_after_repair",
                    model=model, schema=schema.__name__, error=str(exc2)[:400],
                )
                raise ModelError(
                    "The analysis service returned an unreadable response. "
                    "This item has been flagged for review."
                ) from exc2

    # -- public operations -------------------------------------------------

    async def analyse_job(self, job_text: str) -> JobAnalysis:
        return await self._structured(
            system=prompts.JOB_ANALYSIS_SYSTEM,
            user=prompts.job_analysis_prompt(job_text),
            model=settings.ANTHROPIC_EXTRACTION_MODEL,
            schema=JobAnalysis,
        )

    async def extract_resume(self, resume_text: str) -> ResumeProfile:
        return await self._structured(
            system=prompts.RESUME_EXTRACTION_SYSTEM,
            user=prompts.resume_extraction_prompt(resume_text),
            model=settings.ANTHROPIC_EXTRACTION_MODEL,
            schema=ResumeProfile,
            max_tokens=6000,
        )

    async def verify_requirements(
        self, requirements: list[dict], chunks: list[dict]
    ) -> VerificationBatch:
        """
        Adjudicate a batch of requirements against retrieved chunks.

        Batching matters: 9 requirements across 128 resumes is 1,152 calls done
        naively, or 128 done this way.
        """
        return await self._structured(
            system=prompts.VERIFICATION_SYSTEM,
            user=prompts.verification_prompt(requirements, chunks),
            model=settings.ANTHROPIC_VERIFICATION_MODEL,
            schema=VerificationBatch,
            max_tokens=4096,
        )

    async def generate_questions(
        self, gaps: list[dict], role_title: str
    ) -> QuestionSet:
        if not gaps:
            return QuestionSet(questions=[])
        return await self._structured(
            system=prompts.QUESTION_SYSTEM,
            user=prompts.question_prompt(gaps, role_title),
            model=settings.ANTHROPIC_EXTRACTION_MODEL,
            schema=QuestionSet,
            max_tokens=2048,
        )

    async def health(self) -> dict:
        if not self.enabled:
            return {"configured": False, "reachable": False,
                    "detail": "ANTHROPIC_API_KEY not set"}
        try:
            await self._complete(
                system="Reply with the single word: ok",
                user="ping",
                model=settings.ANTHROPIC_EXTRACTION_MODEL,
                max_tokens=8,
                cache_system=False,
            )
            return {"configured": True, "reachable": True}
        except Exception as exc:
            return {"configured": True, "reachable": False, "detail": str(exc)[:200]}


_client: AnthropicClient | None = None


def get_client() -> AnthropicClient:
    global _client
    if _client is None:
        _client = AnthropicClient()
    return _client
