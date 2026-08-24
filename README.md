<div align="center">

# ResumeIQ

**Resume screening that shows its working.**

Every verdict traces to a quoted line from the resume. Every gap states what was
searched for and not found. No opaque percentage.

![Tests](https://img.shields.io/badge/tests-421%20passing-brightgreen)
![Python](https://img.shields.io/badge/python-3.12-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688)
![React](https://img.shields.io/badge/React-18-61dafb)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16%20%2B%20pgvector-336791)
![License](https://img.shields.io/badge/license-MIT-lightgrey)

</div>

---

Built to explore one question: **can an AI screening tool be made defensible
enough that a recruiter could justify a rejection to the candidate?**

Most of what follows is about where it failed and what that taught me. The stack
is further down, where it belongs.

---

## Contents

- [The bug that mattered most](#the-bug-that-mattered-most)
- [Three other findings](#three-other-findings)
- [The design decision everything rests on](#the-design-decision-everything-rests-on)
- [Architecture](#architecture)
- [The matching cascade](#the-matching-cascade)
- [Scoring model](#scoring-model)
- [Data model](#data-model)
- [Security architecture](#security-architecture)
- [What is verified, and what is not](#what-is-verified-and-what-is-not)
- [Running it](#running-it)
- [Why there is no live deployment](#why-there-is-no-live-deployment)

---

## The bug that mattered most

While reading through a candidate report I found this:

> **CI/CD security, vulnerability management, and incident response**
> *No evidence found* — "No mention of CI/CD security, vulnerability management,
> incident response, or security incident handling found in the submitted resume."

Two lines below, on the same page, the system quoted her resume as evidence for
a different requirement:

> *"Cloud security engineer with 5+ years... partnering with DevOps teams on
> identity, logging, **vulnerability management, and incident response**."*

The system asserted a phrase was absent from a document, then quoted that phrase
from that document. Its own report disproved it.

### Why it happened

The verifier only ever sees *retrieved chunks*, not the whole resume. When it
reports "not found", the honest reading is "not found in what I was shown". The
code rendered that as "no mention in the submitted resume" — a claim about the
document the system had no basis to make.

### Why it took four attempts

Absence claims were emitted from four independent places. Each fix looked
correct until a real document took a different route.

| # | Path | Why it was missed |
|---|---|---|
| 1 | `ground_verdict` | The obvious one — LLM output handling |
| 2 | `_no_evidence` | Fires when retrieval returns nothing; bypasses layer 1 |
| 3 | `deterministic_pass` | Checked a *curated skill dictionary*, never the requirement's own extracted aliases — then returned `confidence=0.9`. Its comment read "genuinely absent across every alias" and was wrong. |
| 4 | The database column | The UI renders `absence_statement`, which regenerated the claim and discarded the grader's reasoning |

Path 3 is the one worth remembering. **Confidently wrong is worse than
uncertain**, and the comment made the error look considered.

### The fix

Three distinct outcomes where there was one:

```
Term absent from the whole document
  → "No mention of X, Y, or Z found in the submitted resume"

Term present but no described work
  → "Mentioned (vulnerability management, incident response) but with
     no supporting description of the work"

Model cited text that cannot be located
  → "The supporting quote could not be verified against the document"
```

Each is true of what the system actually knows. Tests assert the property at all
four entry points rather than in one implementation, so a fifth path cannot
reintroduce it quietly.

---

## Three other findings

### A cheaper model silently destroyed the evidence layer

Switching evidence verification from Sonnet to Haiku took a well-qualified
candidate from **4/5 to 0/5**.

Every positive verdict must cite text that is fuzzy-matched against the source
at 0.88 similarity. Haiku *paraphrases* evidence; Sonnet quotes it. All five
citations scored 0.40–0.68 and were correctly rejected as ungrounded.

The guard worked exactly as designed. The problem is that the system then
reported five confident absences and completed normally. A recruiter would have
rejected a qualified candidate and seen nothing amiss.

**Model choice is not a free cost lever. It does not degrade gracefully — it
collapses.**

### Requirement structure drives accuracy more than the model does

Same system, same day, two job descriptions:

| | Recall of "strong" in top 10 |
|---|---|
| **JD01** — Cloud Security Engineer | **11%** |
| **JD02** — Full-Stack Engineer | **89%** |

The difference was one requirement:

> *CI/CD security, vulnerability management, and incident response*

Three disciplines bundled into one, satisfied by mentioning *any* of them
convincingly — so it rewards breadth over depth, the inverse of what a recruiter
wants. Eight of nine strong candidates failed on it while every medium candidate
passed by name-checking all three.

**Accuracy work belongs upstream in how requirements are structured, not
downstream in ranking weights.** Weights only reorder *within* a coverage tier;
all the error was *at* the tier boundary, where weights cannot reach.

### Sampling temperature was left at its default

The same job description produced 7, then 5, then 8 requirements across three
runs — one invented from the *Responsibilities* section rather than the
requirements. `temperature` had never been set, so it defaulted to 1.0.

A ranking someone has to defend cannot rest on a die roll.

---

## The design decision everything rests on

**Coverage is a gate, not a score.**

```
        ┌──────────────────────────────┐
        │  Meets every must-have       │   ← weights reorder within a tier
        ├──────────────────────────────┤
        │  One requirement short       │   ← a candidate can never cross
        ├──────────────────────────────┤     a boundary by scoring well
        │  Multiple gaps               │
        └──────────────────────────────┘
```

A candidate missing a must-have stays in a lower tier no matter how strong they
are elsewhere. Enforced structurally, with 13 tests attacking it using extreme
weight vectors.

This is what makes the output explicable. *"Ranked third because he has eight of
nine requirements"* is a sentence a recruiter can say out loud. *"Ranked third,
79% match"* is not.

Other decisions follow from the same principle:

| Decision | Rationale |
|---|---|
| **Evidence is grounded or discarded** | A verdict whose quote cannot be located is downgraded to no-evidence, not accepted with a caveat. Fabricated evidence is worse than none because it looks convincing. |
| **Hedged language is capped** | "Familiar with Kubernetes" cannot exceed 0.2 regardless of model output. A resume listing every JD keyword with no described work scored **1/9**. |
| **Identity separated by database grant** | The worker role has `INSERT` but no `SELECT` on `candidate_identities`. The scoring pipeline *physically cannot* read a name. Contact details are extracted by regex, never by a model. |
| **Unreadable documents are quarantined** | A scanned PDF with no text layer is held out with an explanation, not ranked last for reasons resembling a judgement about the person. |
| **No sentiment, photographs, institutions, or graduation dates** | Each is a protected-characteristic proxy. Employment gaps are measured for context and never scored. |

---

## Architecture

Modular monolith with a worker queue. Deliberately not microservices — nothing
about this workload justifies the operational cost.

```
                        ┌─────────────┐
        Browser ───────►│   React     │  Vite · TanStack Query
                        │   SPA       │  URL owns filter state
                        └──────┬──────┘
                               │ JWT (access 15m / refresh 14d)
                               ▼
    ┌──────────────────────────────────────────────────────┐
    │                     FastAPI                          │
    │  ┌────────┐  ┌───────────┐  ┌────────┐  ┌─────────┐  │
    │  │  auth  │  │ screening │  │ upload │  │ reports │  │
    │  └────────┘  └───────────┘  └────────┘  └─────────┘  │
    │   tiered rate limits · org-scoped queries · audit    │
    └───┬─────────────┬──────────────┬──────────────┬──────┘
        │             │              │              │
        ▼             ▼              ▼              ▼
  ┌──────────┐  ┌──────────┐  ┌───────────┐  ┌──────────┐
  │ Postgres │  │  Redis   │  │  S3 /     │  │  ClamAV  │
  │ pgvector │  │   arq    │  │  MinIO    │  │  sidecar │
  └────▲─────┘  └────┬─────┘  └─────▲─────┘  └────▲─────┘
       │             │              │             │
       │             ▼              │             │
       │      ┌──────┴──────────────┴─────────────┴──────┐
       └──────┤              Worker (arq)                 │
  INSERT only │  extract → gate → chunk → embed → match   │
  on identity │  → verify → score → rank                  │
              └──────────────┬────────────────────────────┘
                             │ sanitised text only
                             ▼
                    ┌────────────────┐
                    │  Anthropic API │  never receives name,
                    │  Haiku/Sonnet  │  email, phone, address
                    └────────────────┘
```

### Screening pipeline

```
Upload
  ├── extension + magic-byte validation
  ├── size + zip-bomb guard
  ├── malware scan (fail-closed in production)
  └── SHA-256 dedupe
        │
        ▼
Text extraction ── PyMuPDF · python-docx · LibreOffice · Tesseract OCR
  │                all local; no document leaves the host here
  ▼
Quality gate ──── no text layer? → QUARANTINE with reason,
  │                held out of the ranking rather than scored low
  ▼
Contact extraction (regex, local) ──► candidate_identities
  │                                    worker: INSERT, no SELECT
  ▼
Chunk + embed ─── bge-base-en-v1.5, local, 768-dim → pgvector HNSW
  │
  ▼
Matching cascade ─ four layers, cheapest first
  │
  ▼
Evidence rows ─── quote, char offsets, page, grade, method, model version
  │
  ▼
Scoring ───────── coverage tier, then weighted score within tier
```

Re-scoring reads stored evidence only. No document is reopened, no model is
called — fast enough to drive a slider.

---

## The matching cascade

Each layer is an order of magnitude more expensive than the last, so cheap
layers resolve what they can and only genuine ambiguity reaches the model.

| Layer | Method | Cost | Resolves |
|---|---|---|---|
| **1** | Curated alias dictionary | free | Exact and near-exact skill matches |
| **2** | BM25 lexical | free | Term-frequency relevance |
| **3** | Embeddings (`bge-base-en-v1.5`) | local CPU | Semantic similarity — "Postgres" ≈ "PostgreSQL" |
| **4** | LLM adjudication | API call | Genuine ambiguity, and only here |

**The constraint that matters:** layers 1–3 can only ever *propose* evidence.
Only layer 4 issues a verdict, and every verdict must quote text that is then
verified against the source document.

Demonstrated: a candidate writing "Postgres", "k8s" and "RabbitMQ workers" —
never the JD's exact terms — scored 9/9 against a JD asking for PostgreSQL,
Kubernetes and Celery.

---

## Scoring model

### Evidence ladder

A verdict is not binary. Each requirement resolves to a graded band:

| Band | Grade | Meaning |
|---|---|---|
| `direct_with_context` | 1.0 | Described work, with duration or outcome |
| `direct` | 0.8 | Described work |
| `adjacent` | 0.6 | Related but not the named skill |
| `skills_list` | 0.6 | Present in a skills block only |
| `hedged` | **0.2 (capped)** | "Familiar with", "exposure to" |
| `none` | 0.0 | No evidence found |

The hedge cap is applied *after* the model returns, so no verdict can exceed it
regardless of how confident the model was.

### Ranking

```
1. Coverage tier                    ← primary sort key, cannot be overridden
2. Weighted category score within tier
     skills 35% · experience 30% · projects 20%
     education 10% · certifications 5%
3. Deterministic tiebreak on candidate reference
```

Every score is stamped with `scorer_version`, so a ranking can be reproduced
exactly even after the scoring logic changes.

---

## Data model

```
organizations ──┬── users ──── refresh_tokens
                │
                ├── job_descriptions ──── requirements
                │                             │
                └── candidates ───────────────┼──── requirement_evidence
                      │                       │        quote, offsets, page,
                      ├── candidate_identities│        grade, method, model
                      │   (INSERT-only for    │
                      │    the worker role)   └──── candidate_scores
                      │                               tier, score, rank,
                      ├── resume_documents             scorer_version
                      │     └── resume_chunks
                      │           embedding vector(768)
                      │
                      └── screening_batches ──── audit_logs
                                                   append-only, enforced
                                                   by GRANT
```

Two properties worth noting:

**Evidence is a first-class row**, not a derived value. That is what makes
re-scoring free and reproduction possible.

**`audit_logs` has no UPDATE or DELETE grant** for any application role. The
service layer exposes `record()` and nothing else — asserted by a test that
fails if a mutation method is ever added.

---

## Security architecture

| Control | Implementation |
|---|---|
| Passwords | Argon2id, timing-safe comparison |
| Sessions | JWT, 15m access / 14d refresh, rotation with family-reuse revocation |
| Rate limiting | Tiered dependencies — separate limits for auth, reads, writes, uploads, AI operations |
| Cost control | Per-organisation monthly AI budget, checked before enqueueing |
| Tenant isolation | Every query org-scoped; **verified across 18 endpoints** |
| Upload validation | Magic bytes, size caps, zip-bomb guard, path traversal blocked, randomised storage keys |
| Malware | ClamAV sidecar, fail-closed in production |
| Prompt injection | Layered detection; the decisive defence is quote grounding, which makes the attack uneconomic rather than merely detected |
| PII in logs | Resume text never logged — only requirement id, similarity, error category |
| Blind screening | Default on; reveal is a distinct audited action |

**48 security tests**, including adversarial resumes, injection payloads using
zero-width character evasion, and malformed uploads.

---

## What is verified, and what is not

Kept deliberately separate.

### Verified

| | Result |
|---|---|
| Tenant isolation | 18 endpoints, all cross-org access denied — including candidate PII, resume documents, reports |
| Backup restore | Dump → restore to scratch DB → row counts match across 6 tables |
| Malware scanning | Live clamd; EICAR detected, signature logged, file content not logged |
| Rate limiting | Fires at exactly the configured limit under real HTTP traffic |
| Load | 50 concurrent, 3,948 requests, **0% failures**, p95 225ms |
| Connection pool | Bottleneck found by measurement; worst case 5.9s → 2.7s |
| Tests | 421 passing, 44 skipped |

### Not verified

**Accuracy on real resumes.** All testing used synthetic resumes generated to
fit their labels, which I labelled myself. That measures whether the pipeline
discriminates. It is not an accuracy claim and none is made.

Measuring it properly needs two recruiters independently bucketing real resumes,
with inter-rater agreement measured *first* — because where two humans disagree
is the ceiling no system beats. Protocol in [`docs/ranking.md`](docs/ranking.md).

**End-to-end browser testing and a soak test.** Neither run.

---

## Running it

**Requires:** Docker Desktop with 6 GB RAM allocated, and an Anthropic API key.

```bash
git clone https://github.com/syed-ali-hassan-dev/resumeiq.git
cd resumeiq
cp .env.example .env
```

Generate two distinct secrets, then set `SECRET_KEY`, `JWT_SECRET` and
`ANTHROPIC_API_KEY` in `.env`:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

```bash
docker compose up --build          # ~15 min first run
docker compose exec backend python -m app.seed
```

**http://localhost:5173** — `alex.morgan@northwind.demo` / `demo-password-2025`

Seeded with 8 ranked candidates and 4 quarantined documents. No model calls are
needed to explore it.

### Tests

```bash
docker compose exec backend python -m pytest tests/ -q
docker compose -f docker-compose.test.yml up -d
docker compose exec backend python -m pytest tests/integration
```

---

## Why there is no live deployment

Processing real candidate data requires a privacy policy, terms of service, and
a Data Processing Agreement. None exist. Running a public instance where anyone
can upload a resume would mean handling strangers' personal data with no lawful
basis — in employment decision-making, which sits under GDPR Art. 22, NYC Local
Law 144, and the EU AI Act.

[`docs/privacy-data-flow.md`](docs/privacy-data-flow.md) traces what is stored at
every stage, who can read it, and which third party sees it. It exists so a
lawyer can assess it.

The system is *designed* for it — retention enforcement, audited deletion,
append-only audit trail, blind screening by default. Designed-for and cleared-for
are different things.

---

## Documentation

| | |
|---|---|
| [`ranking.md`](docs/ranking.md) | Gate mechanics, evidence ladder, evaluation protocol, known limitations |
| [`security.md`](docs/security.md) | Threat model, controls, regulatory context |
| [`architecture.md`](docs/architecture.md) | Component detail, removed-by-design features |
| [`privacy-data-flow.md`](docs/privacy-data-flow.md) | Stage-by-stage data handling |
| [`performance-baseline.md`](docs/performance-baseline.md) | Measured results, separated from targets |
| [`disaster-recovery.md`](docs/disaster-recovery.md) | RPO/RTO, restore procedure, verification checklist |
| [`api.md`](docs/api.md) | Endpoints with request and response shapes |

---

## What I would do next

1. **Compound-requirement detection** — warn at extraction when a requirement
   bundles separate disciplines; let the recruiter decide whether to split.
   Highest-value accuracy work available, and it needs no model.
2. **A real evaluation** — two recruiters, thirty resumes, agreement measured
   before anything else.
3. **Fail loudly on grounding collapse** — a batch where most citations fail
   validation should error rather than complete with hollow results.
4. **Legal review** before any real data touches it.

---

<div align="center">

*Job descriptions and resumes used in testing are synthetic, created for
evaluation. No real candidate data was processed.*

MIT

</div>
