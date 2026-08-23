"""
The matching cascade.

Four layers, cheapest first. Each narrows what the next must consider, and the
expensive one only runs where the cheap ones leave real ambiguity.

  1. Alias matching   deterministic, free, handles most cases outright
  2. BM25             cheap lexical relevance
  3. Embeddings       semantic retrieval of candidate passages
  4. LLM              adjudicates whether retrieved text actually satisfies

The architectural constraint that matters: layers 1-3 can only ever *propose*
evidence. Only layer 4 issues a verdict, and every verdict it issues must quote
text that is then verified against the source document.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.ai.validation import validate_evidence
from app.core.config import settings
from app.core.logging import get_logger
from app.matching import embeddings as emb
from app.matching.lexical import BM25
from app.matching.normalization import (
    aliases_for, expand_with_implications, find_skills, normalise_skill,
)
from app.scoring.ladder import (
    EvidenceBand, GradedEvidence, Verdict, apply_hedge_cap, band_for_grade,
    grade_for_band, normalise_grade, verdict_for_grade,
)

logger = get_logger(__name__)


@dataclass(slots=True)
class ChunkRef:
    id: str
    text: str
    section: str | None
    page: int | None
    char_start: int
    char_end: int
    embedding: list[float] | None = None

    def as_dict(self) -> dict:
        return {"text": self.text, "section": self.section, "page": self.page}


@dataclass(slots=True)
class RequirementRef:
    id: str
    text: str
    canonical_skill: str | None
    aliases: list[str] = field(default_factory=list)
    kind: str = "skill"

    def search_terms(self) -> list[str]:
        terms = list(self.aliases)
        if self.canonical_skill:
            terms.extend(aliases_for(self.canonical_skill))
        terms.append(self.text)
        seen, out = set(), []
        for term in terms:
            key = term.lower().strip()
            if key and key not in seen:
                seen.add(key)
                out.append(term)
        return out


def absence_statement(requirement: RequirementRef) -> str:
    """
    Say what was searched for, not what the candidate lacks.

    'No mention of WCAG, ARIA, screen readers, or audits' tells the recruiter
    the search was thorough and lets them spot a gap in our alias dictionary.
    'Accessibility: 0%' tells them nothing and reads as a judgement about the
    person.
    """
    terms = [t for t in requirement.search_terms()[:5] if len(t) < 40]
    if len(terms) > 1:
        listed = ", ".join(terms[:-1]) + f", or {terms[-1]}"
    elif terms:
        listed = terms[0]
    else:
        listed = requirement.text
    return f"No mention of {listed} found in the submitted resume."


def _mentions_in_full_text(requirement: RequirementRef, full_text: str) -> list[str]:
    """
    Search terms that appear anywhere in the document.

    The verifier only ever sees retrieved chunks, so a "not found" from it means
    "not found in what I was shown". Rendering that as "no mention in the
    submitted resume" claims more than the system knows — and is falsifiable by
    our own report, which may quote the same line as evidence for a different
    requirement. This check is what keeps an absence statement true.
    """
    if not full_text:
        return []
    haystack = full_text.lower()
    return [
        term for term in requirement.search_terms()[:8]
        if len(term) > 3 and term.lower() in haystack
    ]


def deterministic_pass(
    requirement: RequirementRef, full_text: str, chunks: list[ChunkRef]
) -> GradedEvidence | None:
    """
    Layer 1. Resolve unambiguous cases without spending a model call.

    Only returns a verdict when confident in both directions:
      * strong match in a described role -> skills_list or better
      * nothing at all across every alias -> definitive not_met

    Anything in between returns None and falls through to the LLM.
    """
    canonical = requirement.canonical_skill or normalise_skill(requirement.text)
    if not canonical:
        return None

    found = find_skills(full_text)
    matches = found.for_skill(canonical)

    if not matches:
        proxies = expand_with_implications(found.canonical_skills)
        proxy = next((p for p in proxies if p.canonical == canonical), None)
        if proxy:
            return GradedEvidence(
                requirement_id=requirement.id,
                verdict=Verdict.PARTIAL,
                band=EvidenceBand.STRONG_PROXY,
                grade=grade_for_band(EvidenceBand.STRONG_PROXY),
                quote=None,
                reasoning=(
                    f"{proxy.implied_by} is present, which strongly implies "
                    f"{canonical}, but {canonical} itself is not named."
                ),
                confidence=0.6,
                method="alias",
            )
        # `find_skills` only searches the curated alias dictionary. The
        # requirement's own aliases — produced by extraction, and often
        # multi-word phrases like "vulnerability management" — are never in it,
        # so reaching here does not establish absence. Check them literally
        # before making a claim about the document.
        mentioned = _mentions_in_full_text(requirement, full_text)
        if mentioned:
            return GradedEvidence(
                requirement_id=requirement.id,
                verdict=Verdict.PARTIAL,
                band=EvidenceBand.HEDGED,
                grade=grade_for_band(EvidenceBand.HEDGED),
                quote=None,
                reasoning=(
                    f"Mentioned ({', '.join(mentioned[:3])}) but with no "
                    f"supporting description of the work. Worth confirming at "
                    f"interview."
                ),
                confidence=0.5,
                method="alias",
            )

        # Absent from the dictionary and from the requirement's own terms.
        return GradedEvidence(
            requirement_id=requirement.id,
            verdict=Verdict.NOT_MET,
            band=EvidenceBand.NONE,
            grade=0.0,
            quote=None,
            reasoning=absence_statement(requirement),
            confidence=0.9,
            method="alias",
        )

    # Present. Is it only in a skills list, or described in a role?
    described = [
        c for c in chunks
        if c.section in {"experience", "projects"}
        and any(m.canonical == canonical for m in find_skills(c.text).matches)
    ]
    if not described:
        skills_chunk = next(
            (c for c in chunks if c.section == "skills"
             and any(m.canonical == canonical for m in find_skills(c.text).matches)),
            None,
        )
        if skills_chunk:
            return GradedEvidence(
                requirement_id=requirement.id,
                verdict=Verdict.MET,
                band=EvidenceBand.SKILLS_LIST,
                grade=grade_for_band(EvidenceBand.SKILLS_LIST),
                quote=skills_chunk.text[:300],
                reasoning=f"{canonical} appears in the skills list with no supporting description.",
                confidence=0.8,
                method="alias",
            )

    # Described in a role — grading depth needs the LLM. Fall through.
    return None


async def retrieve(
    requirement: RequirementRef, chunks: list[ChunkRef], bm25: BM25
) -> list[ChunkRef]:
    """
    Layers 2 and 3. Propose the passages most likely to contain evidence.

    Fuses BM25 and embedding ranks. Applies a similarity floor — below it we
    return nothing rather than handing the adjudicator irrelevant text and
    inviting it to rationalise a match.
    """
    if not chunks:
        return []

    query = requirement.text
    if requirement.canonical_skill:
        query = f"{requirement.text} {requirement.canonical_skill}"

    scores: dict[str, float] = {}

    for position, (index, _) in enumerate(bm25.top_k(query, k=settings.RETRIEVAL_TOP_K)):
        if index < len(chunks):
            scores[chunks[index].id] = scores.get(chunks[index].id, 0.0) + 1.0 / (60 + position)

    embedded = [c for c in chunks if c.embedding]
    if embedded:
        try:
            query_vector = (await emb.embed_queries([query]))[0]
            similarities = [
                (c, emb.cosine(query_vector, c.embedding)) for c in embedded
            ]
            similarities = [
                (c, s) for c, s in similarities if s >= settings.RETRIEVAL_MIN_SIMILARITY
            ]
            similarities.sort(key=lambda x: x[1], reverse=True)
            for position, (chunk, _) in enumerate(similarities[: settings.RETRIEVAL_TOP_K]):
                scores[chunk.id] = scores.get(chunk.id, 0.0) + 1.0 / (60 + position)
        except Exception as exc:  # noqa: BLE001
            logger.warning("embedding_retrieval_failed", error=str(exc))

    by_id = {c.id: c for c in chunks}
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [by_id[cid] for cid, _ in ranked[: settings.RETRIEVAL_TOP_K] if cid in by_id]


def ground_verdict(
    raw, requirement: RequirementRef, full_text: str, model_version: str
) -> GradedEvidence:
    """
    Layer 4 output handling. Validate the model's verdict before trusting it.

    Two guards, in order of severity:

    * A positive verdict whose quote cannot be found in the source is downgraded
      to no-evidence, not accepted with a caveat. Fabricated evidence is worse
      than no evidence because it looks convincing in the UI.
    * A zero grade only becomes an absence claim when the term really is absent
      from the whole document. Otherwise it becomes `hedged` — mentioned, not
      evidenced — which is both true and more useful than a flat "not found".
    """
    grade = normalise_grade(raw.evidence_grade)
    quote = raw.evidence_quote

    needs_quote = grade > 0.0 and raw.verdict != "not_met"
    report = validate_evidence(
        quote, full_text, require_quote=needs_quote,
        requirement_ref=requirement.id,
    )

    if needs_quote and not report.valid:
        # Caught the model citing text that is not in the document. We do not
        # trust the rest of its judgement either, so the grade goes to zero —
        # but we must not then claim the requirement is absent, because the
        # only thing established is that this citation was unusable.
        logger.warning(
            "verdict_rejected_ungrounded", requirement=requirement.id,
            reason=report.reason, similarity=report.similarity,
        )
        # An unusable citation says nothing about whether the term appears in
        # the document. Where it does, say so: "mentioned but unverified" is
        # more use to a recruiter than "could not be verified", and both are
        # true.
        mentioned = _mentions_in_full_text(requirement, full_text)
        return GradedEvidence(
            requirement_id=requirement.id,
            verdict=Verdict.NOT_MET,
            band=EvidenceBand.NONE,
            grade=0.0,
            quote=None,
            reasoning=(
                f"Mentioned ({', '.join(mentioned[:3])}), but the supporting "
                f"quote could not be verified against the document."
                if mentioned else
                "The supporting quote could not be located in the submitted "
                "document, so this requirement could not be verified."
            ),
            confidence=0.3,
            method="embedding_llm",
        )

    # Hedged language caps the grade regardless of what the model returned.
    grade = apply_hedge_cap(grade, quote)

    if grade == 0.0:
        mentioned = _mentions_in_full_text(requirement, full_text)
        if mentioned:
            logger.info(
                "absence_downgraded_to_hedged",
                requirement=requirement.id, terms=mentioned[:3],
            )
            return GradedEvidence(
                requirement_id=requirement.id,
                verdict=Verdict.PARTIAL,
                band=EvidenceBand.HEDGED,
                grade=grade_for_band(EvidenceBand.HEDGED),
                quote=None,
                reasoning=(
                    f"Mentioned ({', '.join(mentioned[:3])}) but with no "
                    f"supporting description of the work. Worth confirming at "
                    f"interview."
                ),
                confidence=0.5,
                method="embedding_llm",
            )

        return GradedEvidence(
            requirement_id=requirement.id,
            verdict=Verdict.NOT_MET,
            band=EvidenceBand.NONE,
            grade=0.0,
            quote=None,
            reasoning=absence_statement(requirement),
            confidence=0.5,
            method="embedding_llm",
        )

    return GradedEvidence(
        requirement_id=requirement.id,
        verdict=verdict_for_grade(grade),
        band=band_for_grade(grade),
        grade=grade,
        quote=quote,
        reasoning=raw.reasoning,
        confidence=normalise_grade(raw.confidence),
        method="embedding_llm",
    )
