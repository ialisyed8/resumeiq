"""Cascade behaviour: the deterministic layer and verdict grounding."""
from dataclasses import dataclass

from app.matching.cascade import (
    ChunkRef,
    RequirementRef,
    absence_statement,
    deterministic_pass,
    ground_verdict,
)
from app.matching.lexical import BM25, tokenize
from app.scoring.ladder import EvidenceBand, Verdict

FULL_TEXT = (
    "EXPERIENCE\n"
    "Senior Engineer, Stripe. Rebuilt the Stripe Checkout surface in React 18 "
    "with concurrent rendering and drove it to WCAG 2.1 AA conformance.\n"
    "SKILLS\n"
    "React, TypeScript, GraphQL, Playwright, PostgreSQL\n"
)

CHUNKS = [
    ChunkRef("c1", "Senior Engineer, Stripe. Rebuilt the Stripe Checkout surface in "
                   "React 18 with concurrent rendering and drove it to WCAG 2.1 AA "
                   "conformance.", "experience", 1, 0, 160),
    ChunkRef("c2", "React, TypeScript, GraphQL, Playwright, PostgreSQL", "skills", 1, 161, 220),
]


def req(rid, text, canonical=None, aliases=None):
    return RequirementRef(rid, text, canonical, aliases or [])


class TestAbsenceLanguage:
    def test_names_the_terms_searched(self):
        statement = absence_statement(req("r", "Accessibility", "Accessibility (WCAG)"))
        assert "No mention of" in statement
        assert "wcag" in statement.lower()
        assert "submitted resume" in statement

    def test_never_asserts_incapacity(self):
        statement = absence_statement(req("r", "Kubernetes", "Kubernetes")).lower()
        for phrase in ("does not know", "lacks", "unqualified", "cannot", "incapable"):
            assert phrase not in statement


class TestDeterministicPass:
    def test_absent_skill_is_confident_not_met(self):
        result = deterministic_pass(req("r1", "Kubernetes", "Kubernetes"), FULL_TEXT, CHUNKS)
        assert result is not None
        assert result.verdict is Verdict.NOT_MET
        assert result.grade == 0.0
        assert result.confidence >= 0.8
        assert result.method == "alias"

    def test_skills_list_only_resolved_without_llm(self):
        result = deterministic_pass(req("r2", "PostgreSQL", "PostgreSQL"), FULL_TEXT, CHUNKS)
        assert result is not None
        assert result.band is EvidenceBand.SKILLS_LIST
        assert result.grade == 0.6
        assert result.satisfies

    def test_described_in_role_falls_through_to_llm(self):
        """Grading depth needs judgement, so this must not resolve deterministically."""
        result = deterministic_pass(req("r3", "React", "React"), FULL_TEXT, CHUNKS)
        assert result is None

    def test_proxy_detected_as_partial_not_met(self):
        text = "Built three production apps with Next.js 14 and the app router."
        chunks = [ChunkRef("c", text, "projects", 1, 0, len(text))]
        result = deterministic_pass(req("r4", "React", "React"), text, chunks)
        assert result is not None
        assert result.band is EvidenceBand.STRONG_PROXY
        assert not result.satisfies, "a proxy must never satisfy a must-have alone"

    def test_unknown_skill_returns_none(self):
        assert deterministic_pass(req("r5", "Underwater welding"), FULL_TEXT, CHUNKS) is None


@dataclass
class RawVerdict:
    requirement_id: str
    verdict: str
    evidence_band: str
    evidence_grade: float
    evidence_quote: str | None
    reasoning: str
    confidence: float


class TestVerdictGrounding:
    def test_grounded_quote_accepted(self):
        raw = RawVerdict("r1", "met", "direct_with_context", 1.0,
                         "Rebuilt the Stripe Checkout surface in React 18", "Direct", 0.95)
        result = ground_verdict(raw, req("r1", "React", "React"), FULL_TEXT, "test")
        assert result.verdict is Verdict.MET
        assert result.grade == 1.0

    def test_fabricated_quote_downgraded_to_no_evidence(self):
        raw = RawVerdict("r1", "met", "direct_with_context", 1.0,
                         "Led a team of 50 engineers at Google for eight years",
                         "Strong", 0.99)
        result = ground_verdict(raw, req("r1", "React", "React"), FULL_TEXT, "test")
        assert result.verdict is Verdict.NOT_MET
        assert result.grade == 0.0
        assert result.quote is None

    def test_positive_verdict_without_quote_rejected(self):
        raw = RawVerdict("r1", "met", "direct", 0.8, None, "Seems fine", 0.9)
        result = ground_verdict(raw, req("r1", "React", "React"), FULL_TEXT, "test")
        assert result.verdict is Verdict.NOT_MET

    def test_not_met_needs_no_quote(self):
        raw = RawVerdict("r1", "not_met", "none", 0.0, None, "Nothing found", 0.9)
        result = ground_verdict(raw, req("r1", "Kubernetes", "Kubernetes"), FULL_TEXT, "test")
        assert result.verdict is Verdict.NOT_MET
        assert result.grade == 0.0

    def test_hedged_quote_capped_regardless_of_model_grade(self):
        text = "I am familiar with Kubernetes and container orchestration concepts."
        raw = RawVerdict("r1", "met", "direct", 0.8,
                         "familiar with Kubernetes and container orchestration",
                         "Model was too generous", 0.9)
        result = ground_verdict(raw, req("r1", "Kubernetes", "Kubernetes"), text, "test")
        assert result.grade <= 0.2
        assert not result.satisfies

    def test_out_of_range_grade_clamped(self):
        raw = RawVerdict("r1", "met", "direct", 99.0,
                         "Rebuilt the Stripe Checkout surface in React 18", "x", 5.0)
        result = ground_verdict(raw, req("r1", "React", "React"), FULL_TEXT, "test")
        assert 0.0 <= result.grade <= 1.0
        assert 0.0 <= result.confidence <= 1.0


class TestBM25:
    def test_ranks_relevant_chunk_first(self):
        index = BM25.build([c.text for c in CHUNKS])
        top = index.top_k("accessibility WCAG conformance audits", k=2)
        assert top and top[0][0] == 0

    def test_stopwords_removed(self):
        assert "the" not in tokenize("the quick brown fox")

    def test_empty_corpus_is_safe(self):
        assert BM25.build([]).top_k("anything") == []


class TestAbsenceClaimsAreTrue:
    """
    An absence statement is a factual claim about the candidate's document, and
    it appears in a PDF that gets forwarded to hiring managers. It must never
    assert that something is missing when it is present.

    This was a real defect: the verifier only ever sees retrieved chunks, so a
    "not found" from it meant "not found in what I was shown" — which the report
    then rendered as "no mention in the submitted resume". In one case the same
    report quoted the supposedly-absent phrase two lines further down, as
    evidence for a different requirement.
    """

    SUMMARY = (
        "Cloud security engineer with 5+ years securing AWS environments, "
        "automating controls with Python and Terraform, and partnering with "
        "DevOps teams on identity, logging, vulnerability management, and "
        "incident response.\n"
        "EXPERIENCE\n"
        "Built Terraform modules for VPC, IAM, S3, KMS and CloudTrail."
    )

    def _req(self):
        return RequirementRef(
            "r-cicd",
            "CI/CD security, vulnerability management, and incident response",
            None,
            ["vulnerability management", "incident response", "CI/CD security"],
        )

    def test_mentioned_term_is_not_reported_as_absent(self):
        raw = RawVerdict("r-cicd", "not_met", "none", 0.0, None,
                         "nothing in the retrieved chunks", 0.8)
        result = ground_verdict(raw, self._req(), self.SUMMARY, "test")

        assert result.verdict is not Verdict.NOT_MET, (
            "claimed absent, but the phrase appears in the document"
        )
        assert result.band is EvidenceBand.HEDGED
        assert "no mention" not in (result.reasoning or "").lower()

    def test_reasoning_names_what_was_found(self):
        raw = RawVerdict("r-cicd", "not_met", "none", 0.0, None, "", 0.8)
        result = ground_verdict(raw, self._req(), self.SUMMARY, "test")
        assert "vulnerability management" in (result.reasoning or "")

    def test_genuinely_absent_term_still_reports_absence(self):
        """The guard must not soften real gaps into false partials."""
        raw = RawVerdict("r-k8s", "not_met", "none", 0.0, None, "", 0.9)
        req = RequirementRef("r-k8s", "Kubernetes", "Kubernetes", ["k8s", "EKS"])
        result = ground_verdict(raw, req, self.SUMMARY, "test")

        assert result.verdict is Verdict.NOT_MET
        assert result.grade == 0.0
        assert "no mention" in (result.reasoning or "").lower()

    def test_hedged_grade_does_not_satisfy_a_must_have(self):
        raw = RawVerdict("r-cicd", "not_met", "none", 0.0, None, "", 0.8)
        result = ground_verdict(raw, self._req(), self.SUMMARY, "test")
        assert not result.satisfies, "a bare mention must not pass the gate"

    def test_ungrounded_quote_does_not_produce_an_absence_claim(self):
        """
        A fabricated citation tells us the citation was unusable — not that the
        requirement is absent. The grade still goes to zero.
        """
        raw = RawVerdict("r-cicd", "met", "direct", 0.8,
                         "Ran incident response for a 500-node fleet at Acme",
                         "fabricated", 0.95)
        result = ground_verdict(raw, self._req(), self.SUMMARY, "test")

        assert result.grade == 0.0
        assert result.quote is None
        assert "no mention" not in (result.reasoning or "").lower()
        # Assert the property, not the sentence: the reasoning must say the
        # citation failed, however it is worded.
        assert "could not be verified" in (result.reasoning or "") or \
               "could not be located" in (result.reasoning or "")

    def test_short_terms_are_ignored_to_avoid_false_positives(self):
        """'Go' or 'IaC' would match inside unrelated words."""
        raw = RawVerdict("r-go", "not_met", "none", 0.0, None, "", 0.9)
        req = RequirementRef("r-go", "Go", "Go", ["Go"])
        result = ground_verdict(raw, req, "Ongoing DevOps work at Google.", "test")
        assert result.verdict is Verdict.NOT_MET


class TestNoPathClaimsFalseAbsence:
    """
    Three separate code paths can emit an absence claim, and the bug was fixed
    in them one at a time — each fix appearing to work until a real document
    took a different route. `deterministic_pass` was the last: it checks
    `find_skills`, which only searches the curated alias dictionary, so a
    requirement's own extracted aliases were never literally looked for. The
    comment there read "genuinely absent across every alias" and was wrong.

    These tests pin the property at every entrance rather than in one
    implementation, so a fourth path cannot reintroduce it quietly.
    """

    RESUME = (
        "Cloud security engineer with 5+ years securing AWS environments, "
        "partnering with DevOps teams on identity, logging, vulnerability "
        "management, and incident response.\n"
        "CORE SKILLS: AWS, IAM, CloudTrail, KMS, Python, Terraform, SIEM, ISO 27001\n"
        "EXPERIENCE\nBuilt Terraform modules for VPC, IAM, S3 and KMS."
    )

    def _req(self, rid, text, aliases, canonical=None):
        return RequirementRef(rid, text, canonical, aliases)

    def test_alias_layer_does_not_claim_false_absence(self):
        """deterministic_pass — the path that produced the reported defect."""
        req = self._req(
            "r1", "CI/CD security, vulnerability management, and incident response",
            ["vulnerability management", "incident response"], "incident response",
        )
        result = deterministic_pass(req, self.RESUME, [])
        if result is not None and result.grade == 0.0:
            assert "no mention" not in (result.reasoning or "").lower(), (
                "alias layer claimed absence for a phrase present in the document"
            )

    def test_verifier_layer_does_not_claim_false_absence(self):
        """ground_verdict — the LLM output path."""
        raw = RawVerdict("r2", "not_met", "none", 0.0, None, "not in chunks", 0.8)
        req = self._req("r2", "SIEM platforms", ["SIEM", "security monitoring"])
        result = ground_verdict(raw, req, self.RESUME, "test")
        assert "no mention" not in (result.reasoning or "").lower()

    def test_terms_absent_everywhere_still_report_absence(self):
        """The guard must not soften real gaps."""
        raw = RawVerdict("r3", "not_met", "none", 0.0, None, "", 0.9)
        req = self._req("r3", "Salesforce administration", ["Salesforce", "Apex"])
        result = ground_verdict(raw, req, self.RESUME, "test")
        assert result.verdict is Verdict.NOT_MET
        assert "no mention" in (result.reasoning or "").lower()

    def test_a_mention_never_satisfies_a_must_have(self):
        """
        Honesty about a mention must not become credit for it. A name-drop in a
        summary line is not evidence of the work.
        """
        raw = RawVerdict("r4", "not_met", "none", 0.0, None, "", 0.8)
        req = self._req("r4", "Incident response", ["incident response"])
        result = ground_verdict(raw, req, self.RESUME, "test")
        assert not result.satisfies
