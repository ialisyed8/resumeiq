"""
Development seed data.

Creates a working screening with evidence so the UI can be exercised end to end
without an Anthropic key. Every record is tagged demo so it is distinguishable
from real data.

    python -m app.seed
    python -m app.seed --reset
"""

from __future__ import annotations

import argparse
import asyncio
import random
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

from app.core.logging import configure_logging, get_logger
from app.core.security import hash_password
from app.db.session import SessionFactory
from app.models import (
    AuditLog,
    BatchStatus,
    Candidate,
    CandidateIdentity,
    CandidateScore,
    CoverageTier,
    Decision,
    DecisionAction,
    DocumentStatus,
    ExtractionMethod,
    JobDescription,
    JobStatus,
    MatchMethod,
    Necessity,
    Organization,
    Requirement,
    RequirementEvidence,
    RequirementKind,
    RequirementWeight,
    ResumeChunk,
    ResumeDocument,
    ScreeningBatch,
    ScreeningQuestion,
    User,
    UserRole,
    Verdict,
)
from app.scoring.engine import SCORER_VERSION, CategoryWeights
from app.services.questions import fallback_question

logger = get_logger(__name__)

DEMO_EMAIL = "alex.morgan@northwind.demo"
DEMO_PASSWORD = "demo-password-2025"

JD_TEXT = """Senior Frontend Engineer — Northwind

We are looking for a senior frontend engineer to own our customer-facing web
application, used by several million people each month.

What you'll do
- Build and maintain complex React interfaces
- Own our design system and component library
- Partner with design to ship accessible, performant experiences

What we're looking for
- 5+ years of professional frontend engineering experience
- Deep React expertise, TypeScript in production
- Experience consuming GraphQL APIs
- Demonstrated commitment to accessibility — WCAG 2.1 AA or better
- Strong testing practice, unit and end-to-end
- Track record of performance work at scale
- Experience mentoring or leading other engineers

Nice to have
- AWS, Docker, Next.js
"""

REQUIREMENTS = [
    ("React, 5+ years production", "skill", "must_have", "High", "React",
     ["React.js", "ReactJS", "React 18"]),
    ("TypeScript", "skill", "must_have", "High", "TypeScript", ["TS"]),
    ("5+ years frontend engineering", "experience_duration", "must_have", "High", None, []),
    ("GraphQL", "skill", "must_have", "Medium", "GraphQL", ["Apollo", "Relay"]),
    ("Accessibility (WCAG 2.1 AA)", "skill", "must_have", "High", "Accessibility (WCAG)",
     ["WCAG", "ARIA", "a11y", "screen reader"]),
    ("Testing (unit + E2E)", "skill", "must_have", "Medium", "Testing (unit + E2E)",
     ["Jest", "Playwright", "Cypress"]),
    ("Design systems ownership", "responsibility", "must_have", "Medium", "Design systems",
     ["Storybook", "component library"]),
    ("Performance at scale", "responsibility", "must_have", "Medium", "Performance at scale",
     ["Core Web Vitals", "bundle size"]),
    ("Mentoring or leading engineers", "responsibility", "must_have", "Low", "Team leadership",
     ["mentoring", "tech lead"]),
    ("AWS", "skill", "nice_to_have", "Medium", "AWS", ["EC2", "S3"]),
    ("Docker", "skill", "nice_to_have", "Low", "Docker", ["containers"]),
    ("Next.js", "skill", "nice_to_have", "Medium", "Next.js", ["NextJS"]),
]

# (name, title, years, companies, {requirement_index: (band, grade, quote|None)})
D, DC, SL, PX, H, N = "direct", "direct_with_context", "skills_list", "strong_proxy", "hedged", "none"
GRADES = {DC: 1.0, D: 0.8, SL: 0.6, PX: 0.4, H: 0.2, N: 0.0}

CANDIDATES = [
    ("Sarah Chen", "Senior Frontend Engineer", 8, ["Stripe", "Figma"], {
        0: (DC, "Rebuilt the Stripe Checkout surface in React 18 with concurrent rendering; owned it for 4 of my 8 years."),
        1: (DC, "TypeScript in strict mode across every repository I owned."),
        2: (DC, "8 years frontend, continuous since 2017."),
        3: (DC, "Migrated the payments dashboard from REST to a federated GraphQL gateway."),
        4: (DC, "Drove Checkout to WCAG 2.1 AA; ran quarterly screen-reader audits with NVDA and VoiceOver."),
        5: (DC, "Jest and Playwright suites; raised coverage from 41% to 88% on the payments repo."),
        6: (DC, "Co-maintained Figma's internal component library; 140+ components in Storybook."),
        7: (DC, "Cut Checkout LCP from 3.1s to 1.2s across a 40M-session sample."),
        8: (D, "Tech lead for a team of five; ran hiring loops and mentorship pairings."),
        9: (SL, "S3 and CloudFront for asset delivery."),
        10: (SL, "Docker listed under tooling."),
        11: (D, "Two production Next.js apps with app router and ISR."),
    }),
    ("Amara Okafor", "Staff Frontend Engineer", 9, ["Monzo"], {
        0: (DC, "Nine years of React; led two major version migrations."),
        1: (DC, "Introduced TypeScript to the web platform team in 2019."),
        2: (DC, "9 years frontend engineering."),
        3: (DC, "Designed the schema for Monzo's customer-facing GraphQL layer."),
        4: (DC, "Ran the accessibility working group; shipped WCAG 2.2 AA conformance for the web app."),
        5: (DC, "Playwright E2E across 200+ flows; owns the CI test strategy."),
        6: (DC, "Founded and still maintains the Monzo web design system."),
        7: (D, "Sub-second TTI on the account dashboard."),
        8: (DC, "Line-manages four engineers; runs the frontend guild."),
        9: (SL, "Terraform and AWS for frontend delivery infrastructure."),
        10: (SL, "Containerised the local dev environment."),
    }),
    ("Marcus Reed", "Frontend Engineer", 6, ["Shopify"], {
        0: (DC, "Led the Shopify checkout rewrite in React 18."),
        1: (DC, "Primary language across the last two roles."),
        2: (DC, "6 years frontend, 2019 to present."),
        3: (SL, "Listed under skills; used in two shipped projects."),
        4: (N, None),
        5: (DC, "Jest and Playwright cited in the checkout project."),
        6: (DC, "Built and maintained Polaris component contributions."),
        7: (DC, "Cut bundle size 38% on a 4M-visitor storefront."),
        8: (D, "Mentored two juniors; no formal reports."),
        11: (D, "Storefront rebuilt on Next.js 13."),
    }),
    ("Priya Kumar", "Product Engineer", 7, ["Atlassian"], {
        0: (DC, "Seven years of React across Jira and Confluence surfaces."),
        1: (DC, "TypeScript across all listed roles since 2020."),
        2: (DC, "7 years, continuous."),
        3: (D, "Consumed Atlassian's GraphQL gateway on two products."),
        4: (DC, "Owned the AA accessibility remediation for the Confluence editor; ARIA live regions and keyboard traps."),
        5: (D, "Jest and some Cypress; E2E ownership not described."),
        6: (DC, "Contributed 30+ components to the Atlassian Design System."),
        7: (D, "Reduced editor input latency."),
        8: (N, None),
        10: (SL, "Docker listed under tooling."),
    }),
    ("Tomás Herrera", "Frontend Engineer", 6, ["Cabify"], {
        0: (DC, "Six years React on the rider-facing web app."),
        1: (DC, "TypeScript across the whole codebase."),
        2: (DC, "6 years frontend."),
        3: (D, "Apollo Client on the booking flow."),
        4: (H, "Improved keyboard navigation on one project."),
        5: (D, "Jest unit tests; Cypress mentioned once."),
        6: (D, "Contributed to an internal component library."),
        7: (N, None),
        8: (D, "Onboarded and paired with three new hires."),
        9: (SL, "AWS listed under tooling."),
        10: (D, "Docker Compose for local services."),
        11: (D, "Marketing site on Next.js."),
    }),
    ("David Kim", "UI Engineer", 5, ["Agency"], {
        0: (DC, "Five years building React interfaces for agency clients."),
        1: (D, "TypeScript on the last two client projects."),
        2: (D, "5 years, meets the stated minimum."),
        3: (N, None),
        4: (N, None),
        5: (H, "Familiar with Jest."),
        6: (D, "Built a shared component kit reused across four client sites."),
        7: (D, "Improved Lighthouse scores on marketing sites."),
        8: (H, "Collaborated with junior designers."),
        9: (D, "Deployed on AWS Amplify."),
        11: (DC, "Three client sites built on Next.js."),
    }),
    ("Elena Popov", "Web Developer", 4, ["Freelance"], {
        0: (D, "React on several freelance builds."),
        1: (N, None), 2: (H, "4 years of professional experience."),
        3: (N, None), 4: (H, "Accessible markup claimed once."),
        5: (N, None), 6: (D, "Maintained a Tailwind-based component set."),
        7: (D, "Image optimisation and lazy loading on two builds."),
        8: (N, None), 11: (D, "Two Next.js marketing sites."),
    }),
    ("James Turner", "Junior Developer", 2, ["Bootcamp"], {
        0: (D, "React used across bootcamp capstone and two personal projects."),
        1: (H, "Learning TypeScript."),
        2: (N, None), 3: (N, None), 4: (N, None), 5: (N, None),
        6: (N, None), 7: (N, None), 8: (N, None),
        11: (D, "Portfolio site built with Next.js."),
    }),
]

QUARANTINE_FILES = [
    ("A_Whitfield_CV.pdf", DocumentStatus.QUARANTINED,
     "No usable text layer was found. This looks like a scanned document and needs OCR before it can be screened.",
     0.0, {"has_text_layer": False, "chars_per_page": 12.0, "alpha_ratio": 0.1}),
    ("Portfolio_Resume_Final.pages", DocumentStatus.FAILED,
     ".pages files need converting before they can be read, and conversion is unavailable on this server. Save the document as PDF or DOCX and upload it again.",
     0.0, {"has_text_layer": False}),
    ("R_Nakamura_Resume.pdf", DocumentStatus.QUARANTINED,
     "Extraction confidence 0.31 is below the 0.55 threshold. Held for manual review rather than screened.",
     0.31, {"has_text_layer": True, "alpha_ratio": 0.42, "chars_per_page": 380.0}),
    ("CV_scan_2019.jpg", DocumentStatus.QUARANTINED,
     "Image file. OCR is available but accuracy on photographed pages is limited.",
     0.0, {"has_text_layer": False}),
]


async def reset(session) -> None:
    org = await session.scalar(select(Organization).where(Organization.slug == "northwind-demo"))
    if org:
        await session.execute(delete(AuditLog).where(AuditLog.organization_id == org.id))
        await session.delete(org)
        await session.commit()
        logger.info("demo_data_removed")


async def seed() -> None:
    async with SessionFactory() as session:
        existing = await session.scalar(
            select(Organization).where(Organization.slug == "northwind-demo")
        )
        if existing:
            logger.warning("demo_org_exists", note="Run with --reset to recreate.")
            return

        org = Organization(
            name="Northwind Talent (demo)", slug="northwind-demo",
            settings={"demo": True}, blind_screening_default=True,
        )
        session.add(org)
        await session.flush()

        user = User(
            organization_id=org.id, email=DEMO_EMAIL,
            password_hash=hash_password(DEMO_PASSWORD),
            full_name="Alex Morgan", job_title="Recruiter", role=UserRole.ADMIN,
            email_verified_at=datetime.now(UTC),
        )
        session.add(user)
        await session.flush()

        job = JobDescription(
            organization_id=org.id, title="Senior Frontend Engineer",
            raw_text=JD_TEXT, seniority="Senior", min_years_experience=5,
            status=JobStatus.ACTIVE, created_by=user.id,
            category_weights=CategoryWeights().as_dict(),
        )
        session.add(job)
        await session.flush()

        requirements = []
        for order, (text, kind, necessity, weight, canonical, aliases) in enumerate(REQUIREMENTS):
            req = Requirement(
                job_description_id=job.id, text=text,
                kind=RequirementKind(kind), necessity=Necessity(necessity),
                weight=RequirementWeight(weight), canonical_skill=canonical,
                aliases=aliases, display_order=order, recruiter_edited=False,
            )
            session.add(req)
            requirements.append(req)
        await session.flush()

        batch = ScreeningBatch(
            organization_id=org.id, job_description_id=job.id,
            name="Senior Frontend Engineer — demo batch",
            status=BatchStatus.COMPLETED,
            total_documents=len(CANDIDATES) + len(QUARANTINE_FILES),
            processed_count=len(CANDIDATES),
            quarantined_count=sum(1 for f in QUARANTINE_FILES if f[1] == DocumentStatus.QUARANTINED),
            failed_count=sum(1 for f in QUARANTINE_FILES if f[1] == DocumentStatus.FAILED),
            scorer_version=SCORER_VERSION,
            category_weights=CategoryWeights().as_dict(),
            blind_screening=True,
            model_versions={"note": "seeded demo data — no model calls were made"},
            created_by=user.id,
            started_at=datetime.now(UTC) - timedelta(hours=2),
            completed_at=datetime.now(UTC) - timedelta(hours=1, minutes=48),
        )
        session.add(batch)
        await session.flush()

        from app.scoring.engine import Necessity as SN
        from app.scoring.engine import RequirementSpec, score_batch
        from app.scoring.ladder import EvidenceBand, GradedEvidence
        from app.scoring.ladder import Verdict as LadderVerdict

        specs = [
            RequirementSpec(
                str(r.id), r.text,
                SN.MUST_HAVE if r.necessity is Necessity.MUST_HAVE else SN.NICE_TO_HAVE,
                r.weight.value,
            )
            for r in requirements
        ]
        evidence_map: dict[str, dict] = {}

        for index, (name, title, years, companies, ev_spec) in enumerate(CANDIDATES, start=1):
            candidate = Candidate(
                organization_id=org.id, job_description_id=job.id,
                reference=f"{index:03d}", current_title=title,
                total_experience_months=years * 12,
                structured_profile={"demo": True, "companies": companies},
                decision_status="shortlisted" if index == 2 else "new",
            )
            session.add(candidate)
            await session.flush()

            session.add(CandidateIdentity(
                candidate_id=candidate.id, full_name=name,
                email=f"{name.split()[0].lower()}@example.demo",
            ))

            document = ResumeDocument(
                candidate_id=candidate.id,
                storage_key=f"demo/{uuid.uuid4().hex}.pdf",
                filename=f"{name.replace(' ', '_')}_Resume.pdf",
                mime_type="application/pdf", size_bytes=random.randint(120_000, 400_000),
                file_hash=uuid.uuid4().hex + uuid.uuid4().hex[:32],
                page_count=2, extraction_method=ExtractionMethod.PYMUPDF,
                extraction_confidence=round(random.uniform(0.82, 0.97), 3),
                quality_signals={"has_text_layer": True, "alpha_ratio": 0.83},
                status=DocumentStatus.EXTRACTED,
                extracted_text="\n".join(
                    q for _, q in ev_spec.values() if q
                ) or "Demo resume text.",
                processed_at=datetime.now(UTC),
            )
            session.add(document)
            await session.flush()

            for chunk_index, (_, quote) in enumerate(
                [(k, v[1]) for k, v in sorted(ev_spec.items()) if v[1]]
            ):
                session.add(ResumeChunk(
                    resume_document_id=document.id, chunk_index=chunk_index,
                    text=quote, section="experience", page=1,
                    char_start=chunk_index * 200, char_end=chunk_index * 200 + len(quote),
                ))

            graded: dict[str, GradedEvidence] = {}
            for req_index, requirement in enumerate(requirements):
                band_name, quote = ev_spec.get(req_index, (N, None))
                band = EvidenceBand(band_name)
                grade = GRADES[band_name]
                verdict = (
                    LadderVerdict.MET if grade >= 0.6
                    else LadderVerdict.PARTIAL if grade >= 0.2
                    else LadderVerdict.NOT_MET
                )
                absence = None
                if grade == 0.0:
                    terms = requirement.aliases[:4] or [requirement.text]
                    listed = ", ".join(terms[:-1]) + f", or {terms[-1]}" if len(terms) > 1 else terms[0]
                    absence = f"No mention of {listed} found in the submitted resume."

                session.add(RequirementEvidence(
                    screening_batch_id=batch.id, candidate_id=candidate.id,
                    requirement_id=requirement.id,
                    verdict=Verdict(verdict.value), evidence_band=band.value,
                    evidence_grade=grade, evidence_quote=quote,
                    evidence_page=1 if quote else None,
                    evidence_char_start=0 if quote else None,
                    evidence_char_end=len(quote) if quote else None,
                    absence_statement=absence,
                    reasoning=absence or "Evidence located in the submitted resume.",
                    confidence=0.9 if grade >= 0.8 else 0.7 if grade > 0 else 0.85,
                    method=MatchMethod.ALIAS if grade in (0.0, 0.6) else MatchMethod.EMBEDDING_LLM,
                    model_version=None, quote_validated=bool(quote),
                ))

                graded[str(requirement.id)] = GradedEvidence(
                    requirement_id=str(requirement.id), verdict=verdict,
                    band=band, grade=grade, quote=quote,
                )

                if grade < 0.6 and requirement.necessity is Necessity.MUST_HAVE:
                    session.add(ScreeningQuestion(
                        screening_batch_id=batch.id, candidate_id=candidate.id,
                        requirement_id=requirement.id,
                        question=fallback_question(requirement.text, requirement.canonical_skill),
                        rationale=absence or "Evidence found is indirect — worth confirming.",
                    ))

            evidence_map[str(candidate.id)] = graded

            if index == 2:
                session.add(Decision(
                    screening_batch_id=batch.id, candidate_id=candidate.id,
                    user_id=user.id, action=DecisionAction.SHORTLIST,
                    previous_status="new",
                    note="Strong accessibility and design system evidence.",
                    coverage_at_decision="9/9", scorer_version=SCORER_VERSION,
                ))

        # Quarantined documents get candidate records but no scores — they are
        # held out of the ranking, not ranked low.
        for offset, (filename, doc_status, reason, confidence, signals) in enumerate(
            QUARANTINE_FILES, start=len(CANDIDATES) + 1
        ):
            candidate = Candidate(
                organization_id=org.id, job_description_id=job.id,
                reference=f"{offset:03d}", decision_status="new",
            )
            session.add(candidate)
            await session.flush()
            session.add(CandidateIdentity(candidate_id=candidate.id))
            session.add(ResumeDocument(
                candidate_id=candidate.id, storage_key=f"demo/{uuid.uuid4().hex}",
                filename=filename, mime_type="application/pdf", size_bytes=250_000,
                file_hash=uuid.uuid4().hex + uuid.uuid4().hex[:32],
                page_count=2, extraction_confidence=confidence,
                quality_signals=signals, status=doc_status,
                quarantine_reason=reason,
            ))

        await session.flush()

        scores = score_batch(specs, evidence_map, CategoryWeights())
        for score in scores:
            session.add(CandidateScore(
                screening_batch_id=batch.id,
                candidate_id=uuid.UUID(score.candidate_id),
                must_haves_met=score.must_haves_met,
                must_haves_total=score.must_haves_total,
                coverage_tier=CoverageTier(score.tier.value),
                must_have_score=score.must_have_score,
                nice_to_have_bonus=score.nice_to_have_bonus,
                final_score=score.final_score, rank=score.rank,
                missing_requirements=score.missing_requirements,
                scorer_version=SCORER_VERSION,
                category_weights=CategoryWeights().as_dict(),
            ))

        for action, note in (
            ("screening.completed", "Demo batch seeded"),
            ("candidate.shortlisted", "Strong accessibility and design system evidence."),
        ):
            session.add(AuditLog(
                organization_id=org.id, actor_id=user.id, actor_label="Alex Morgan",
                action=action, entity_type="screening", entity_id=batch.id,
                note=note, scorer_version=SCORER_VERSION,
            ))

        await session.commit()

        print("\n  Demo data seeded.\n")
        print(f"    Email:     {DEMO_EMAIL}")
        print(f"    Password:  {DEMO_PASSWORD}")
        print(f"    Screening: {batch.id}")
        print(f"    Ranked:    {len(scores)} candidates")
        print(f"    Held for review: {len(QUARANTINE_FILES)} documents")
        print(f"    Scorer:    {SCORER_VERSION}\n")
        print("  All records are marked as demo data. No model calls were made.\n")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Seed ResumeIQ demo data")
    parser.add_argument("--reset", action="store_true", help="Remove existing demo data first")
    args = parser.parse_args()

    configure_logging()
    if args.reset:
        async with SessionFactory() as session:
            await reset(session)
    await seed()


if __name__ == "__main__":
    asyncio.run(main())
