"""
The screening pipeline.

Runs in a worker process, not in an HTTP request. Stages:

    extract -> quality gate -> structure -> chunk+embed -> match -> score

Two properties this file exists to guarantee:

1. **Failure is isolated and visible.** One corrupted PDF quarantines that
   candidate and the batch continues. A quarantined candidate is never scored,
   because a document we could not read must not be presented as a weak
   applicant.

2. **Every intermediate artefact is persisted.** Extracted text, chunks,
   embeddings, and per-requirement evidence all land in the database. Scoring
   then reads only from there, which is what makes re-scoring instant and makes
   any historical ranking reproducible.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.ai.client import get_client
from app.ai.validation import detect_injection, find_quote
from app.core.config import settings
from app.core.logging import get_logger, job_id_var
from app.db.session import SessionFactory
from app.extraction.chunker import chunk_resume
from app.extraction.contact import extract_contact
from app.extraction.router import extract_document
from app.matching.cascade import (
    ChunkRef, RequirementRef, absence_statement, deterministic_pass,
    ground_verdict, retrieve,
)
from app.matching.lexical import BM25
from app.matching import embeddings as emb
from app.models import (
    BatchStatus, Candidate, CandidateIdentity, DocumentStatus, ExtractionMethod,
    JobDescription, MatchMethod, Requirement, RequirementEvidence, ResumeChunk,
    ResumeDocument, ScreeningBatch, ScreeningQuestion, Verdict as VerdictEnum,
)
from app.scoring.experience import RoleExperience, parse_range, total_experience_months
from app.services import audit
from app.services.questions import fallback_question, rationale_for
from app.services.screening import rescore
from app.services.storage import storage

logger = get_logger(__name__)

METHOD_MAP = {
    "alias": MatchMethod.ALIAS,
    "lexical": MatchMethod.LEXICAL,
    "embedding_llm": MatchMethod.EMBEDDING_LLM,
    "llm_direct": MatchMethod.LLM_DIRECT,
}

EXTRACTION_METHOD_MAP = {
    "pymupdf": ExtractionMethod.PYMUPDF,
    "python_docx": ExtractionMethod.PYTHON_DOCX,
    "libreoffice": ExtractionMethod.LIBREOFFICE,
    "ocr_tesseract": ExtractionMethod.OCR_TESSERACT,
    "ocr_vision": ExtractionMethod.OCR_VISION,
}


async def run_screening(batch_id: str) -> dict:
    """Entry point invoked by the queue."""
    job_id_var.set(batch_id)
    logger.info("screening_started", batch=batch_id)

    async with SessionFactory() as session:
        batch = await session.scalar(
            select(ScreeningBatch).where(ScreeningBatch.id == uuid.UUID(batch_id))
        )
        if batch is None:
            logger.error("batch_missing", batch=batch_id)
            return {"status": "missing"}

        batch.status = BatchStatus.EXTRACTING
        batch.started_at = datetime.now(timezone.utc)
        batch.model_versions = {
            "extraction": settings.ANTHROPIC_EXTRACTION_MODEL,
            "verification": settings.ANTHROPIC_VERIFICATION_MODEL,
            "embedding": settings.EMBEDDING_MODEL,
        }
        await session.commit()

    try:
        await _extract_stage(batch_id)
        await _match_stage(batch_id)
        await _score_stage(batch_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("screening_failed", batch=batch_id)
        async with SessionFactory() as session:
            batch = await session.scalar(
                select(ScreeningBatch).where(ScreeningBatch.id == uuid.UUID(batch_id))
            )
            if batch:
                batch.status = BatchStatus.FAILED
                batch.error_message = (
                    "Screening could not be completed. Candidate data has been "
                    "preserved and the batch can be retried."
                )
                await audit.record(
                    session, organization_id=batch.organization_id,
                    action=audit.Action.SCREENING_FAILED, entity_type="screening",
                    entity_id=batch.id, note=str(exc)[:500],
                )
                await session.commit()
        return {"status": "failed", "error": str(exc)[:200]}

    logger.info("screening_completed", batch=batch_id)
    return {"status": "completed"}


# ---------------------------------------------------------------------------
# Stage 1 — extraction, quality gate, structure
# ---------------------------------------------------------------------------

async def _extract_stage(batch_id: str) -> None:
    async with SessionFactory() as session:
        batch = await session.scalar(
            select(ScreeningBatch).where(ScreeningBatch.id == uuid.UUID(batch_id))
        )
        documents = (
            await session.scalars(
                select(ResumeDocument)
                .join(Candidate, Candidate.id == ResumeDocument.candidate_id)
                .where(
                    Candidate.job_description_id == batch.job_description_id,
                    Candidate.deleted_at.is_(None),
                    ResumeDocument.status.in_(
                        [DocumentStatus.PENDING, DocumentStatus.PROCESSING]
                    ),
                )
            )
        ).all()
        document_ids = [str(d.id) for d in documents]

    client = get_client()

    for document_id in document_ids:
        async with SessionFactory() as session:
            document = await session.scalar(
                select(ResumeDocument).where(ResumeDocument.id == uuid.UUID(document_id))
            )
            batch = await session.scalar(
                select(ScreeningBatch).where(ScreeningBatch.id == uuid.UUID(batch_id))
            )
            candidate = await session.scalar(
                select(Candidate).where(Candidate.id == document.candidate_id)
            )

            try:
                document.status = DocumentStatus.PROCESSING
                await session.commit()

                local_path = storage.download_to_temp(
                    document.storage_key, suffix=f".{document.filename.rsplit('.', 1)[-1]}"
                )
                result = await extract_document(local_path, document.filename)

                document.extracted_text = result.text
                document.page_count = result.page_count
                document.extraction_confidence = result.quality.confidence
                document.quality_signals = result.quality.as_dict()
                document.extraction_method = EXTRACTION_METHOD_MAP.get(result.method)
                document.processed_at = datetime.now(timezone.utc)

                # White text and micro-fonts are excluded from what we screen.
                if result.hidden_text:
                    document.injection_flags = [
                        f"hidden_text: {h['reason']}" for h in result.hidden_text[:10]
                    ]
                    logger.warning(
                        "hidden_text_excluded", document=document_id,
                        count=len(result.hidden_text),
                    )

                injected, patterns = detect_injection(result.text)
                if injected:
                    document.injection_flags = (document.injection_flags or []) + [
                        f"injection: {p}" for p in patterns[:5]
                    ]
                    await audit.record(
                        session, organization_id=batch.organization_id,
                        action=audit.Action.INJECTION_DETECTED, entity_type="document",
                        entity_id=document.id,
                        note=f"Instruction-like content found in {document.filename}",
                        new_value={"patterns": patterns[:5]},
                    )

                # Contact details go to the identity table and nowhere else.
                # Extracted with regex rather than a model so identifying data
                # never enters the model path, and written here rather than into
                # structured_profile so the scorer cannot read it back.
                contact = extract_contact(result.text)
                if any((contact.full_name, contact.email, contact.phone)):
                    identity = await session.scalar(
                        select(CandidateIdentity).where(
                            CandidateIdentity.candidate_id == candidate.id
                        )
                    )
                    if identity is None:
                        session.add(
                            CandidateIdentity(
                                candidate_id=candidate.id, **contact.as_kwargs()
                            )
                        )
                    else:
                        # Only fill blanks: a recruiter correction must survive
                        # a re-run of the pipeline.
                        identity.full_name = identity.full_name or contact.full_name
                        identity.email = identity.email or contact.email
                        identity.phone = identity.phone or contact.phone
                        identity.links = identity.links or (contact.links or {})

                if result.quarantined:
                    document.status = DocumentStatus.QUARANTINED
                    document.quarantine_reason = result.quality.reason
                    batch.quarantined_count += 1
                    await audit.record(
                        session, organization_id=batch.organization_id,
                        action=audit.Action.RESUME_QUARANTINED, entity_type="document",
                        entity_id=document.id, note=result.quality.reason,
                    )
                    logger.info("document_quarantined", document=document_id,
                                reason=result.quality.reason)
                    await session.commit()
                    continue

                # Chunk and embed.
                chunks = chunk_resume(result.text, result.page_breaks)
                vectors = []
                if chunks:
                    try:
                        vectors = await emb.embed_passages([c.text for c in chunks])
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("embedding_failed", document=document_id,
                                       error=str(exc)[:200])
                        vectors = [None] * len(chunks)

                for chunk, vector in zip(chunks, vectors or [None] * len(chunks)):
                    session.add(
                        ResumeChunk(
                            resume_document_id=document.id, chunk_index=chunk.index,
                            text=chunk.text, section=chunk.section, page=chunk.page,
                            char_start=chunk.char_start, char_end=chunk.char_end,
                            embedding=vector,
                        )
                    )

                # Structured profile via the model, when configured.
                if client.enabled:
                    profile = await client.extract_resume(result.text)
                    candidate.structured_profile = profile.model_dump()
                    candidate.current_title = profile.current_title

                    roles = []
                    for role in profile.roles:
                        start, end, _ = parse_range(role.date_range or "")
                        roles.append(
                            RoleExperience(
                                title=role.title, organisation=role.organisation,
                                start=start, end=end,
                                technologies=role.technologies,
                                raw_range=role.date_range,
                            )
                        )
                    candidate.total_experience_months = total_experience_months(roles)

                document.status = DocumentStatus.EXTRACTED
                batch.processed_count += 1
                await session.commit()

            except Exception as exc:  # noqa: BLE001
                logger.exception("document_failed", document=document_id)
                await session.rollback()
                async with SessionFactory() as recovery:
                    doc = await recovery.scalar(
                        select(ResumeDocument).where(
                            ResumeDocument.id == uuid.UUID(document_id)
                        )
                    )
                    bat = await recovery.scalar(
                        select(ScreeningBatch).where(
                            ScreeningBatch.id == uuid.UUID(batch_id)
                        )
                    )
                    if doc and bat:
                        doc.status = DocumentStatus.FAILED
                        doc.quarantine_reason = (
                            "This document could not be processed. It has been held "
                            "for manual review rather than ranked."
                        )
                        bat.failed_count += 1
                        await recovery.commit()


# ---------------------------------------------------------------------------
# Stage 2 — matching and evidence verification
# ---------------------------------------------------------------------------

async def _match_stage(batch_id: str) -> None:
    async with SessionFactory() as session:
        batch = await session.scalar(
            select(ScreeningBatch).where(ScreeningBatch.id == uuid.UUID(batch_id))
        )
        batch.status = BatchStatus.VERIFYING
        await session.commit()

        requirements = (
            await session.scalars(
                select(Requirement)
                .where(Requirement.job_description_id == batch.job_description_id)
                .order_by(Requirement.display_order)
            )
        ).all()
        refs = [
            RequirementRef(
                id=str(r.id), text=r.text, canonical_skill=r.canonical_skill,
                aliases=list(r.aliases or []), kind=r.kind.value,
            )
            for r in requirements
        ]

        # Only extracted documents are matched. Quarantined ones are excluded
        # entirely rather than scored as empty.
        pairs = (
            await session.execute(
                select(Candidate.id, ResumeDocument.id)
                .join(ResumeDocument, ResumeDocument.candidate_id == Candidate.id)
                .where(
                    Candidate.job_description_id == batch.job_description_id,
                    Candidate.deleted_at.is_(None),
                    ResumeDocument.status == DocumentStatus.EXTRACTED,
                )
            )
        ).all()
        work = [(str(c), str(d)) for c, d in pairs]

    client = get_client()

    for candidate_id, document_id in work:
        try:
            await _verify_candidate(batch_id, candidate_id, document_id, refs, client)
        except Exception:  # noqa: BLE001
            logger.exception("verification_failed", candidate=candidate_id)


async def _verify_candidate(
    batch_id: str, candidate_id: str, document_id: str, refs, client
) -> None:
    async with SessionFactory() as session:
        document = await session.scalar(
            select(ResumeDocument).where(ResumeDocument.id == uuid.UUID(document_id))
        )
        chunk_rows = (
            await session.scalars(
                select(ResumeChunk)
                .where(ResumeChunk.resume_document_id == document.id)
                .order_by(ResumeChunk.chunk_index)
            )
        ).all()

        full_text = document.extracted_text or ""
        chunks = [
            ChunkRef(
                id=str(c.id), text=c.text, section=c.section, page=c.page,
                char_start=c.char_start, char_end=c.char_end,
                embedding=list(c.embedding) if c.embedding is not None else None,
            )
            for c in chunk_rows
        ]
        bm25 = BM25.build([c.text for c in chunks])

        resolved: dict[str, object] = {}
        needs_llm: list[RequirementRef] = []

        # Layer 1 — deterministic. Resolves the clear cases for free.
        for ref in refs:
            verdict = deterministic_pass(ref, full_text, chunks)
            if verdict is not None:
                resolved[ref.id] = verdict
            else:
                needs_llm.append(ref)

        # Layers 2-4 — retrieve then adjudicate, batched per candidate.
        if needs_llm and client.enabled:
            retrieved: dict[str, list[ChunkRef]] = {}
            for ref in needs_llm:
                retrieved[ref.id] = await retrieve(ref, chunks, bm25)

            unique: dict[str, ChunkRef] = {}
            for group in retrieved.values():
                for chunk in group:
                    unique[chunk.id] = chunk

            batch_size = settings.VERIFICATION_BATCH_SIZE
            for start in range(0, len(needs_llm), batch_size):
                window = needs_llm[start : start + batch_size]
                payload = [
                    {"id": r.id, "text": r.text, "aliases": r.search_terms()[:8]}
                    for r in window
                ]
                relevant = [
                    unique[c.id].as_dict()
                    for r in window
                    for c in retrieved.get(r.id, [])
                    if c.id in unique
                ]
                seen, deduped = set(), []
                for item in relevant:
                    key = item["text"][:80]
                    if key not in seen:
                        seen.add(key)
                        deduped.append(item)

                if not deduped:
                    for ref in window:
                        resolved[ref.id] = _no_evidence(ref, full_text)
                    continue

                response = await client.verify_requirements(payload, deduped)
                by_id = {v.requirement_id: v for v in response.verdicts}
                for ref in window:
                    raw = by_id.get(ref.id)
                    if raw is None:
                        resolved[ref.id] = _no_evidence(ref, full_text)
                        continue
                    resolved[ref.id] = ground_verdict(
                        raw, ref, full_text, settings.ANTHROPIC_VERIFICATION_MODEL
                    )
        else:
            for ref in needs_llm:
                resolved[ref.id] = _no_evidence(ref, full_text)

        # Persist evidence with source offsets for the highlight viewer.
        for ref in refs:
            graded = resolved.get(ref.id)
            if graded is None:
                graded = _no_evidence(ref, full_text)

            page = char_start = char_end = None
            validated = False
            if graded.quote:
                found, span, _ = find_quote(graded.quote, full_text)
                validated = found
                if found and span:
                    char_start, char_end = span
                    page = next(
                        (c.page for c in chunks
                         if c.char_start <= char_start <= c.char_end), None
                    )

            session.add(
                RequirementEvidence(
                    screening_batch_id=uuid.UUID(batch_id),
                    candidate_id=uuid.UUID(candidate_id),
                    requirement_id=uuid.UUID(ref.id),
                    verdict=VerdictEnum(graded.verdict.value),
                    evidence_band=graded.band.value,
                    evidence_grade=graded.grade,
                    evidence_quote=graded.quote,
                    evidence_page=page,
                    evidence_char_start=char_start,
                    evidence_char_end=char_end,
                    # This column is what the UI and the PDF render, so it must
                    # carry the reasoning the grader actually arrived at.
                    # Regenerating absence_statement() here discarded that and
                    # re-asserted "no mention in the submitted resume" — even
                    # when the grader had established only that a quote could
                    # not be verified, or that the term was present but
                    # undescribed. The display path was the last place the false
                    # claim survived after the three grading paths were fixed.
                    absence_statement=(
                        (graded.reasoning or absence_statement(ref))
                        if graded.grade == 0.0 else None
                    ),
                    reasoning=graded.reasoning,
                    confidence=graded.confidence,
                    method=METHOD_MAP.get(graded.method, MatchMethod.NOT_EVALUATED),
                    model_version=(
                        settings.ANTHROPIC_VERIFICATION_MODEL
                        if graded.method == "embedding_llm" else None
                    ),
                    quote_validated=validated,
                )
            )

        await session.commit()

    await _generate_questions(batch_id, candidate_id, refs, resolved, client)


def _no_evidence(ref: RequirementRef, full_text: str = ""):
    """
    Result for a requirement the verifier never returned a verdict on.

    Reached when retrieval surfaces nothing, or the model omits a requirement
    from its response. Both mean "we did not examine this properly" — which is
    not the same as "the resume does not contain it", and must not be reported
    as though it were.

    So the full document is checked before any absence is claimed. If the terms
    are present, this becomes a hedged partial: true, and more useful to a
    recruiter than a flat "not found". Same guarantee as ground_verdict, which
    this path bypasses.
    """
    from app.matching.cascade import _mentions_in_full_text
    from app.scoring.ladder import EvidenceBand, GradedEvidence, Verdict, grade_for_band

    mentioned = _mentions_in_full_text(ref, full_text) if full_text else []
    if mentioned:
        return GradedEvidence(
            requirement_id=ref.id,
            verdict=Verdict.PARTIAL,
            band=EvidenceBand.HEDGED,
            grade=grade_for_band(EvidenceBand.HEDGED),
            quote=None,
            reasoning=(
                f"Mentioned ({', '.join(mentioned[:3])}) but with no supporting "
                f"description of the work. Worth confirming at interview."
            ),
            confidence=0.5,
            method="alias",
        )

    return GradedEvidence(
        requirement_id=ref.id, verdict=Verdict.NOT_MET, band=EvidenceBand.NONE,
        grade=0.0, quote=None, reasoning=absence_statement(ref),
        confidence=0.5, method="alias",
    )


async def _generate_questions(batch_id, candidate_id, refs, resolved, client) -> None:
    """Turn gaps into interview prompts, never into verdicts."""
    gaps = [
        r for r in refs
        if (g := resolved.get(r.id)) is not None and not g.satisfies
    ]
    if not gaps:
        return

    async with SessionFactory() as session:
        batch = await session.scalar(
            select(ScreeningBatch).where(ScreeningBatch.id == uuid.UUID(batch_id))
        )
        job = await session.scalar(
            select(JobDescription).where(JobDescription.id == batch.job_description_id)
        )

        generated: dict[str, tuple[str, str | None]] = {}
        if client.enabled:
            try:
                # Use the graded reasoning rather than regenerating an absence
                # statement. A hedged requirement — mentioned but not described
                # — is a gap worth a question, but saying "no mention of X" when
                # X is in the resume would be false, and this text is shown to
                # the recruiter beside the question.
                payload = [
                    {
                        "id": r.id,
                        "text": r.text,
                        "absence": (
                            (resolved.get(r.id).reasoning if resolved.get(r.id) else None)
                            or absence_statement(r)
                        ),
                    }
                    for r in gaps[:8]
                ]
                response = await client.generate_questions(payload, job.title)
                generated = {
                    q.requirement_id: (q.question, q.rationale)
                    for q in response.questions
                }
            except Exception as exc:  # noqa: BLE001
                logger.warning("question_generation_failed", error=str(exc)[:200])

        for ref in gaps[:8]:
            question, rationale = generated.get(
                ref.id,
                (fallback_question(ref.text, ref.canonical_skill),
                 rationale_for(
                     (resolved.get(ref.id).reasoning if resolved.get(ref.id) else None)
                     or absence_statement(ref)
                 )),
            )
            session.add(
                ScreeningQuestion(
                    screening_batch_id=uuid.UUID(batch_id),
                    candidate_id=uuid.UUID(candidate_id),
                    requirement_id=uuid.UUID(ref.id),
                    question=question, rationale=rationale,
                )
            )
        await session.commit()


# ---------------------------------------------------------------------------
# Stage 3 — scoring
# ---------------------------------------------------------------------------

async def _score_stage(batch_id: str) -> None:
    async with SessionFactory() as session:
        batch = await session.scalar(
            select(ScreeningBatch).where(ScreeningBatch.id == uuid.UUID(batch_id))
        )
        batch.status = BatchStatus.SCORING
        await session.commit()

        scores = await rescore(session, batch)

        batch.status = BatchStatus.COMPLETED
        batch.completed_at = datetime.now(timezone.utc)
        await audit.record(
            session, organization_id=batch.organization_id,
            action=audit.Action.SCREENING_COMPLETED, entity_type="screening",
            entity_id=batch.id, scorer_version=batch.scorer_version,
            new_value={
                "scored": len(scores),
                "quarantined": batch.quarantined_count,
                "failed": batch.failed_count,
            },
        )
        await session.commit()
        logger.info("scoring_complete", batch=batch_id, candidates=len(scores))
