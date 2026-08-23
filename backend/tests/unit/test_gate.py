"""
The gate property is the core promise of the product: a candidate who meets
every mandatory requirement outranks one who does not, no matter how many
preferred skills the latter accumulates. These tests exist to make that
property impossible to regress.
"""
import pytest

from app.scoring.engine import (
    BONUS_CAP, CategoryWeights, CoverageTier, Necessity, RequirementSpec,
    rank_candidates, score_batch, score_candidate, tier_for,
)
from app.scoring.ladder import EvidenceBand, GradedEvidence, Verdict, grade_for_band


def ev(req_id, band, method="test"):
    grade = grade_for_band(band)
    return GradedEvidence(
        requirement_id=req_id,
        verdict=Verdict.MET if grade >= 0.6 else (Verdict.PARTIAL if grade >= 0.2 else Verdict.NOT_MET),
        band=band,
        grade=grade,
        quote="evidence" if grade > 0 else None,
        method=method,
    )


@pytest.fixture
def reqs():
    musts = [
        RequirementSpec(f"m{i}", f"Must {i}", Necessity.MUST_HAVE, "High")
        for i in range(1, 5)
    ]
    nices = [
        RequirementSpec(f"n{i}", f"Nice {i}", Necessity.NICE_TO_HAVE, "High")
        for i in range(1, 6)
    ]
    return musts + nices


class TestTierAssignment:
    def test_full_coverage_is_top_tier(self):
        assert tier_for(9, 9) is CoverageTier.MEETS_ALL

    def test_one_short(self):
        assert tier_for(8, 9) is CoverageTier.ONE_SHORT

    def test_two_short_is_multiple_gaps(self):
        assert tier_for(7, 9) is CoverageTier.MULTIPLE_GAPS

    def test_zero_requirements_does_not_crash(self):
        assert tier_for(0, 0) is CoverageTier.MEETS_ALL


class TestGateCannotBeBought:
    def test_all_nice_to_haves_cannot_beat_one_missing_must(self, reqs):
        """The scenario from the brief, stated as a test."""
        complete = {r.id: ev(r.id, EvidenceBand.SKILLS_LIST) for r in reqs if r.necessity is Necessity.MUST_HAVE}
        # Meets every must-have, but at the weakest passing grade and zero extras.
        gapped = {
            r.id: ev(r.id, EvidenceBand.DIRECT_WITH_CONTEXT)
            for r in reqs
            if r.necessity is Necessity.MUST_HAVE and r.id != "m1"
        }
        # ...and every nice-to-have at maximum strength.
        gapped.update({
            r.id: ev(r.id, EvidenceBand.DIRECT_WITH_CONTEXT)
            for r in reqs if r.necessity is Necessity.NICE_TO_HAVE
        })

        ranked = score_batch(reqs, {"complete": complete, "loaded": gapped})
        assert ranked[0].candidate_id == "complete"
        assert ranked[0].tier is CoverageTier.MEETS_ALL
        assert ranked[1].tier is CoverageTier.ONE_SHORT
        # And the gapped candidate scores higher on raw points, proving the tier
        # ordering — not the score — is what protects the complete candidate.
        assert ranked[1].final_score > ranked[0].final_score

    def test_bonus_is_capped(self, reqs):
        ev_map = {r.id: ev(r.id, EvidenceBand.DIRECT_WITH_CONTEXT) for r in reqs}
        score = score_candidate("c", reqs, ev_map)
        assert score.nice_to_have_bonus <= BONUS_CAP

    def test_missing_evidence_never_passes(self, reqs):
        score = score_candidate("empty", reqs, {})
        assert score.must_haves_met == 0
        assert score.tier is CoverageTier.MULTIPLE_GAPS
        assert len(score.missing_requirements) == 4

    def test_hedged_evidence_does_not_satisfy(self, reqs):
        ev_map = {r.id: ev(r.id, EvidenceBand.HEDGED) for r in reqs}
        score = score_candidate("hedged", reqs, ev_map)
        assert score.must_haves_met == 0

    def test_skills_list_mention_does_satisfy(self, reqs):
        ev_map = {r.id: ev(r.id, EvidenceBand.SKILLS_LIST) for r in reqs}
        score = score_candidate("listed", reqs, ev_map)
        assert score.must_haves_met == 4

    def test_proxy_alone_does_not_satisfy(self, reqs):
        ev_map = {r.id: ev(r.id, EvidenceBand.STRONG_PROXY) for r in reqs}
        score = score_candidate("proxy", reqs, ev_map)
        assert score.must_haves_met == 0
        assert score.tier is CoverageTier.MULTIPLE_GAPS


class TestWeightsStayInsideTier:
    def test_extreme_weights_cannot_reorder_across_tiers(self, reqs):
        full = {r.id: ev(r.id, EvidenceBand.SKILLS_LIST) for r in reqs if r.necessity is Necessity.MUST_HAVE}
        short = {
            r.id: ev(r.id, EvidenceBand.DIRECT_WITH_CONTEXT)
            for r in reqs if r.necessity is Necessity.MUST_HAVE and r.id != "m1"
        }
        for weights in (
            CategoryWeights(skills=100, experience=0, projects=0, education=0, certifications=0),
            CategoryWeights(skills=0, experience=0, projects=0, education=0, certifications=100),
            CategoryWeights(skills=1, experience=1, projects=1, education=1, certifications=1),
        ):
            ranked = score_batch(reqs, {"full": full, "short": short}, weights)
            assert ranked[0].candidate_id == "full", f"tier violated with {weights}"


class TestRankingStability:
    def test_ranking_is_deterministic(self, reqs):
        pool = {
            f"c{i}": {r.id: ev(r.id, EvidenceBand.DIRECT) for r in reqs[:i]}
            for i in range(1, 6)
        }
        first = [s.candidate_id for s in score_batch(reqs, pool)]
        second = [s.candidate_id for s in score_batch(reqs, dict(reversed(list(pool.items()))))]
        assert first == second

    def test_ranks_are_sequential(self, reqs):
        pool = {f"c{i}": {r.id: ev(r.id, EvidenceBand.DIRECT) for r in reqs[:i]} for i in range(1, 5)}
        ranked = score_batch(reqs, pool)
        assert [s.rank for s in ranked] == [1, 2, 3, 4]
