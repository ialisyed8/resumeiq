# Ranking methodology

The scoring engine is `backend/app/scoring/engine.py`. Its behaviour is fixed by
`tests/unit/test_gate.py` and `tests/eval/test_ranking_quality.py` — if you
change the maths, those tests are the specification you are changing.

## Why not a single weighted score

A single weighted percentage produces the failure this product exists to avoid:
a candidate with many preferred technologies outranking one who meets every
mandatory requirement. You cannot fix that by tuning weights, because any weight
vector that lets preferred skills contribute at all lets them accumulate past a
missing mandatory one. It has to be fixed structurally.

## Stage 1 — the coverage gate

Must-have requirements are evaluated first. A candidate missing one does not get
a deduction; they get a different **tier**:

| Must-haves short | Tier | Label | Match level |
|---|---|---|---|
| 0 | `meets_all` | Meets every must-have | Strong Match |
| 1 | `one_short` | One requirement short | Worth a Look |
| 2+ | `multiple_gaps` | Multiple gaps | Below Bar |

Tier is the **primary sort key**. Score is a tiebreaker *within* a tier. This is
what makes the guarantee structural: sorting cannot cross a tier boundary, and
the UI groups rows under tier headers so the boundary is visible.

## Stage 2 — the evidence ladder

Each requirement gets a grade in [0, 1] from an explicit ladder
(`app/scoring/ladder.py`):

| Grade | Band | Meaning |
|---|---|---|
| 1.0 | `direct_with_context` | Describes doing it, with duration or outcome |
| 0.8 | `direct` | Describes doing it, without duration |
| 0.6 | `skills_list` | Named in a skills list only |
| 0.4 | `strong_proxy` | Closely implied (Next.js → React) |
| 0.2 | `hedged` | "familiar with", "exposure to", "coursework in" |
| 0.0 | `none` | No supporting text found |

Grades at or above **0.6** satisfy the gate.

Two guards run after the model returns a grade:

- **Hedge cap.** If the supporting quote contains hedging language, the grade is
  capped at 0.2 regardless of what the model said. Models are reliably too
  generous here: asked whether someone knows Kubernetes, given "familiar with
  Kubernetes", they routinely answer 0.8.
- **Quote grounding.** A positive verdict whose quote cannot be located in the
  source document is discarded and downgraded to no-evidence. Fabricated
  evidence is worse than no evidence because it looks convincing in the UI.

## Aggregation

```
must_have_score = Σ(weight_i × grade_i) / Σ(weight_i)      over must-haves
bonus           = min(10, nice_to_have_fraction × 10)      capped
final           = must_have_score × 90 + bonus
```

Requirement weights map High/Medium/Low → 1.0/0.65/0.35.

The bonus is capped and applied *after* the tier is decided, so it can only
reorder candidates who are already in the same tier.

## Recruiter category weights

The five sliders (skills, experience, projects, education, certifications)
reorder candidates within a tier. The UI says so explicitly, and
`test_extreme_weights_cannot_reorder_across_tiers` proves it for weight vectors
including `(100,0,0,0,0)` and `(0,0,0,0,100)`.

## Re-scoring

`POST /api/screenings/{id}/rescore` reads stored evidence, applies new weights,
and rewrites ranks. It does not open a PDF or call a model, so it returns in
milliseconds and can drive an interactive slider.

This is only possible because evidence is persisted as first-class rows in
`requirement_evidence` rather than collapsed into a score. That single decision
is what makes the system tunable *and* reproducible.

## Scorer versioning

`SCORER_VERSION` is stamped on every `candidate_scores` row. A new version
writes new rows rather than mutating old ones, so:

- any historical ranking can be reproduced exactly,
- a new scorer can be replayed against an old batch and diffed,
- an audit entry can name the exact scoring logic in force at decision time.

Bump it whenever scoring behaviour changes.

## Presentation

Coverage is the primary number and it is presented as a **count**, not a
percentage. The segmented meter renders one tick per requirement precisely so
that "8 of 9" reads as countable rather than as a measurement with an implied
error bar.

The score is shown as a bare integer and treated as ordinal. We do not claim a
3-point difference is meaningful, because it is not.

## Evaluation

`tests/eval/` measures ranking against a labelled fixture pool.

| Metric | Why |
|---|---|
| **Recall of strong @20** | The one that matters. A qualified person at rank 60 is never seen. |
| Precision@10 | Is the first screenful worth the recruiter's time? |
| NDCG@10, @20 | Rank-aware quality, weights the top |
| Mean rank of strong | Directly measures burial |
| Kendall's τ | Overall correlation with intended order |

Extraction accuracy is measured **separately** (`extraction_metrics`). Without
that split, a ranking regression sends you tuning weights when the real problem
is that two-column PDFs are parsing wrong.

### Building a real gold set

The fixture pool is synthetic and exists to catch regressions. For real
evaluation:

1. Take 3–5 real job descriptions, 50–100 resumes each.
2. Have two or more recruiters independently bucket each candidate as
   **strong** / **possible** / **no**. Buckets, not rankings — humans are
   unreliable at ordering 80 candidates and quite consistent at bucketing them.
3. Measure inter-rater agreement (Cohen's κ). If your recruiters disagree with
   each other, that is your realistic accuracy ceiling.
4. Run ablations: remove embeddings, remove the LLM layer, alias-matching only.
   Teams routinely discover the embedding layer adds under two points of NDCG
   over good lexical matching — worth knowing before optimising it.

Recruiter overrides (`requirement_evidence.override_verdict`) accumulate as
labelled disagreements during normal use, and are the cheapest source of real
evaluation data you will get.

## Known limitations

**Skills-list mentions sit exactly at the pass threshold.** A resume that lists
every required technology without describing any work will reach a high coverage
count. Its *score* is low, so it sorts to the bottom of its tier, but the
coverage number alone would mislead. If this matters for your roles, raise
`MET_THRESHOLD` in `ladder.py` above 0.6 so a skills-list mention no longer
satisfies a must-have — at the cost of more false negatives on terse resumes.

**Experience duration is only as good as date parsing.** `parse_range` handles
the formats we have seen; unusual ones return `None` and the role is skipped
rather than counted as zero. Check `total_experience_months` against reality on
your own corpus before trusting duration requirements.

**The proxy table is hand-maintained.** `SKILL_IMPLICATIONS` encodes judgements
(Next.js implies React) that are true today and may not be in three years.
