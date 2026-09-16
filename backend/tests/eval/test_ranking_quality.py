"""
Ranking quality regression suite.

Uses a fixture pool with known-correct labels. These tests fail when a scoring
change moves a qualified candidate down, which is exactly the regression that is
otherwise invisible until a recruiter complains.
"""
import json
import pathlib

import pytest

from app.scoring.engine import (
    CategoryWeights,
    CoverageTier,
    Necessity,
    RequirementSpec,
    score_batch,
)
from app.scoring.ladder import EvidenceBand, GradedEvidence, Verdict, grade_for_band
from tests.eval.metrics import evaluate, extraction_metrics, ndcg_at_k, recall_at_k

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def load_fixture(name):
    return json.loads((FIXTURES / name).read_text())


def build_evidence(bands: dict) -> dict:
    out = {}
    for req_id, band_name in bands.items():
        band = EvidenceBand(band_name)
        grade = grade_for_band(band)
        out[req_id] = GradedEvidence(
            requirement_id=req_id,
            verdict=(
                Verdict.MET if grade >= 0.6
                else Verdict.PARTIAL if grade >= 0.2
                else Verdict.NOT_MET
            ),
            band=band, grade=grade,
            quote="evidence" if grade > 0 else None,
        )
    return out


@pytest.fixture
def fixture_set():
    return load_fixture("frontend_engineer.json")


@pytest.fixture
def specs(fixture_set):
    return [
        RequirementSpec(
            r["id"], r["text"],
            Necessity.MUST_HAVE if r["necessity"] == "must_have" else Necessity.NICE_TO_HAVE,
            r["weight"],
        )
        for r in fixture_set["requirements"]
    ]


@pytest.fixture
def ranked(fixture_set, specs):
    evidence = {
        c["id"]: build_evidence(c["evidence"]) for c in fixture_set["candidates"]
    }
    return score_batch(specs, evidence)


class TestRankingQuality:
    def test_every_strong_candidate_surfaces_in_top_20(self, fixture_set, ranked):
        labels = {c["id"]: c["label"] for c in fixture_set["candidates"]}
        result = evaluate([s.candidate_id for s in ranked], labels)
        assert result.recall_strong_at_20 == 1.0, (
            f"A qualified candidate was buried: {result.as_dict()}"
        )

    def test_ndcg_above_threshold(self, fixture_set, ranked):
        labels = {c["id"]: c["label"] for c in fixture_set["candidates"]}
        result = evaluate([s.candidate_id for s in ranked], labels)
        assert result.ndcg_at_10 >= 0.85, result.as_dict()

    def test_no_candidate_ranks_above_a_better_tier(self, ranked):
        order = [CoverageTier.MEETS_ALL, CoverageTier.ONE_SHORT, CoverageTier.MULTIPLE_GAPS]
        seen = [order.index(s.tier) for s in ranked]
        assert seen == sorted(seen), "tier ordering violated"

    def test_adjacent_domain_candidate_is_not_penalised_for_vocabulary(
        self, fixture_set, ranked
    ):
        """
        The case the whole product exists for: a genuinely qualified person whose
        resume uses different words should still surface.
        """
        target = next(
            c for c in fixture_set["candidates"] if c["id"] == "adjacent_vocab"
        )
        assert target["label"] == "strong"
        position = next(i for i, s in enumerate(ranked) if s.candidate_id == "adjacent_vocab")
        assert position < 5, f"qualified candidate ranked {position + 1}"

    def test_keyword_stuffed_resume_does_not_top_the_list(self, ranked):
        """Listing every keyword with no described work must not beat real evidence."""
        stuffer = next(i for i, s in enumerate(ranked) if s.candidate_id == "keyword_stuffer")
        genuine = next(i for i, s in enumerate(ranked) if s.candidate_id == "sarah_chen")
        assert genuine < stuffer

    def test_weight_changes_do_not_break_tier_ordering(self, specs, fixture_set):
        evidence = {
            c["id"]: build_evidence(c["evidence"]) for c in fixture_set["candidates"]
        }
        order = [CoverageTier.MEETS_ALL, CoverageTier.ONE_SHORT, CoverageTier.MULTIPLE_GAPS]
        for weights in (
            CategoryWeights(100, 0, 0, 0, 0),
            CategoryWeights(0, 100, 0, 0, 0),
            CategoryWeights(20, 20, 20, 20, 20),
        ):
            ranked = score_batch(specs, evidence, weights)
            seen = [order.index(s.tier) for s in ranked]
            assert seen == sorted(seen)


class TestMetrics:
    def test_ndcg_perfect_ordering(self):
        assert ndcg_at_k([2, 2, 1, 1, 0, 0], 6) == pytest.approx(1.0)

    def test_ndcg_penalises_inversion(self):
        assert ndcg_at_k([0, 0, 1, 1, 2, 2], 6) < 0.6

    def test_recall_finds_all_strong(self):
        assert recall_at_k([2, 1, 0, 2], 4) == 1.0

    def test_recall_misses_buried_strong(self):
        assert recall_at_k([1, 0, 0, 2], 2) == 0.0

    def test_no_strong_candidates_is_vacuously_perfect(self):
        assert recall_at_k([0, 0, 1], 5) == 1.0


class TestExtractionMetrics:
    def test_perfect_extraction(self):
        truth = {"a": "met", "b": "not_met"}
        result = extraction_metrics({"a": "met", "b": "not_met"}, truth)
        assert result["precision"] == 1.0 and result["recall"] == 1.0

    def test_false_positive_lowers_precision(self):
        truth = {"a": "met", "b": "not_met"}
        result = extraction_metrics({"a": "met", "b": "met"}, truth)
        assert result["precision"] < 1.0
        assert result["false_positives"] == 1

    def test_false_negative_lowers_recall(self):
        truth = {"a": "met", "b": "met"}
        result = extraction_metrics({"a": "met", "b": "not_met"}, truth)
        assert result["recall"] < 1.0
        assert result["false_negatives"] == 1
