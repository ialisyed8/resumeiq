"""
Ranking evaluation metrics.

The primary metric is recall of strong candidates in the top 20. A qualified
person at rank 60 is never seen, and that is the failure mode this whole system
exists to avoid. NDCG and precision are reported alongside because they respond
differently to changes and disagreement between them is informative.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

#: Recruiter labels, ordinal. Humans bucket reliably; they do not rank 80
#: candidates reliably.
RELEVANCE = {"strong": 2, "possible": 1, "no": 0}


@dataclass(slots=True)
class EvalResult:
    recall_strong_at_20: float
    precision_at_10: float
    ndcg_at_10: float
    ndcg_at_20: float
    mean_rank_of_strong: float
    kendall_tau: float
    n_candidates: int
    n_strong: int

    def as_dict(self) -> dict:
        return {
            "recall_strong@20": round(self.recall_strong_at_20, 4),
            "precision@10": round(self.precision_at_10, 4),
            "ndcg@10": round(self.ndcg_at_10, 4),
            "ndcg@20": round(self.ndcg_at_20, 4),
            "mean_rank_of_strong": round(self.mean_rank_of_strong, 2),
            "kendall_tau": round(self.kendall_tau, 4),
            "n_candidates": self.n_candidates,
            "n_strong": self.n_strong,
        }


def dcg(relevances: list[int], k: int) -> float:
    return sum(
        (2 ** rel - 1) / math.log2(i + 2)
        for i, rel in enumerate(relevances[:k])
    )


def ndcg_at_k(ranked_labels: list[int], k: int) -> float:
    ideal = sorted(ranked_labels, reverse=True)
    denominator = dcg(ideal, k)
    return (dcg(ranked_labels, k) / denominator) if denominator else 0.0


def recall_at_k(ranked_labels: list[int], k: int, target: int = 2) -> float:
    total = sum(1 for rel in ranked_labels if rel >= target)
    if not total:
        return 1.0
    found = sum(1 for rel in ranked_labels[:k] if rel >= target)
    return found / total


def precision_at_k(ranked_labels: list[int], k: int, target: int = 1) -> float:
    window = ranked_labels[:k]
    if not window:
        return 0.0
    return sum(1 for rel in window if rel >= target) / len(window)


def kendall_tau(ranked_labels: list[int]) -> float:
    """Rank correlation against the ideal ordering by label."""
    n = len(ranked_labels)
    if n < 2:
        return 1.0
    concordant = discordant = 0
    for i in range(n):
        for j in range(i + 1, n):
            a, b = ranked_labels[i], ranked_labels[j]
            if a > b:
                concordant += 1
            elif a < b:
                discordant += 1
    total = concordant + discordant
    return ((concordant - discordant) / total) if total else 1.0


def evaluate(ranked_candidate_ids: list[str], labels: dict[str, str]) -> EvalResult:
    """
    Score one ranking against recruiter labels.

    `ranked_candidate_ids` is the system's output order; `labels` maps candidate
    id to strong/possible/no.
    """
    ranked = [RELEVANCE.get(labels.get(cid, "no"), 0) for cid in ranked_candidate_ids]
    strong_positions = [
        i + 1 for i, cid in enumerate(ranked_candidate_ids)
        if labels.get(cid) == "strong"
    ]
    return EvalResult(
        recall_strong_at_20=recall_at_k(ranked, 20),
        precision_at_10=precision_at_k(ranked, 10),
        ndcg_at_10=ndcg_at_k(ranked, 10),
        ndcg_at_20=ndcg_at_k(ranked, 20),
        mean_rank_of_strong=(
            sum(strong_positions) / len(strong_positions) if strong_positions else 0.0
        ),
        kendall_tau=kendall_tau(ranked),
        n_candidates=len(ranked_candidate_ids),
        n_strong=len(strong_positions),
    )


def extraction_metrics(predicted: dict, truth: dict) -> dict:
    """
    Per-requirement extraction precision and recall.

    Measured separately from ranking. Without this split, a ranking regression
    sends you tuning weights when the real problem is two-column PDFs parsing
    wrong.
    """
    tp = fp = fn = 0
    for key, actual in truth.items():
        got = predicted.get(key, "not_met")
        actual_met = actual == "met"
        got_met = got == "met"
        if got_met and actual_met:
            tp += 1
        elif got_met and not actual_met:
            fp += 1
        elif not got_met and actual_met:
            fn += 1
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "precision": round(precision, 4), "recall": round(recall, 4),
        "f1": round(f1, 4), "true_positives": tp,
        "false_positives": fp, "false_negatives": fn,
    }
