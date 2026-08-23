# Architecture

## Shape

A modular monolith with one background worker. Not microservices — the domains
here share a database and a transaction boundary, and splitting them would buy
deployment complexity in exchange for nothing.

```
                    ┌─────────────┐
                    │   Browser   │
                    └──────┬──────┘
                           │ HTTPS only
                    ┌──────▼──────┐
                    │    nginx    │  TLS, security headers, rate limits,
                    │   (proxy)   │  SSE passthrough
                    └──┬───────┬──┘
              static   │       │  /api
              ┌────────▼──┐ ┌──▼────────────┐
              │ frontend  │ │   FastAPI     │
              │ React SPA │ │   backend     │
              └───────────┘ └──┬─────┬──┬───┘
                               │     │  │
                 ┌─────────────┘     │  └──────────┐
                 │                   │             │
          ┌──────▼──────┐   ┌────────▼───┐  ┌──────▼──────┐
          │ PostgreSQL  │   │   Redis    │  │  S3/MinIO   │
          │ + pgvector  │   │ queue+cache│  │  documents  │
          └──────▲──────┘   └────────▲───┘  └──────▲──────┘
                 │                   │             │
                 └────────┬──────────┴─────────────┘
                          │
                   ┌──────▼──────┐        ┌──────────────┐
                   │ arq worker  ├───────►│ Anthropic API│
                   │  (pipeline) │        └──────────────┘
                   └─────────────┘
```

**The browser never reaches Postgres, Redis, or Anthropic.** Only the backend
and worker hold the model API key. This is the reason the key is a server-side
environment variable and never a build-time constant.

## Backend modules

| Module | Responsibility |
|---|---|
| `api/v1/` | HTTP surface. Thin — validation and orchestration only |
| `core/` | Config, security, errors, logging, deps, rate limiting |
| `models/` | SQLAlchemy ORM, one file, all tables |
| `ai/` | Anthropic client, prompts, response schemas, **validation** |
| `extraction/` | PDF, DOCX, OCR, conversion, quality gate, chunking |
| `matching/` | Normalisation, BM25, embeddings, the four-layer cascade |
| `scoring/` | Evidence ladder, ranking engine, experience calculation |
| `services/` | Storage, audit, screening orchestration, questions |
| `workers/` | arq entry point and the pipeline |

`extraction`, `matching`, and `scoring` have no FastAPI or database imports.
They take plain data and return plain data, which is what makes them directly
unit-testable and why `tests/unit/` runs in under a second without any
infrastructure.

## The pipeline

```
upload ──► validate (magic bytes, size, zip bomb) ──► object storage
                                                          │
   ┌──────────────────────────────────────────────────────┘
   ▼
extract text  (PyMuPDF layout-aware / python-docx XML walk / LibreOffice / OCR)
   │
   ▼
quality gate ──► fails? ──► QUARANTINE (never scored, shown separately)
   │ passes
   ▼
chunk semantically ──► embed ──► store chunks + vectors
   │
   ▼
structured extraction (model) ──► experience calculation
   │
   ▼
for each requirement:
   layer 1  alias match          → resolves most cases, free
   layer 2  BM25                 → cheap lexical retrieval
   layer 3  embeddings           → semantic retrieval
   layer 4  model adjudication   → verdict + quote
            │
            ▼
       quote grounding ──► fails? ──► downgrade to no-evidence
   │
   ▼
persist requirement_evidence (verdict, grade, quote, offsets, method, version)
   │
   ▼
score ──► gate to tier ──► rank ──► persist candidate_scores (versioned)
```

Each stage writes its output to the database before the next begins. That is
what makes the pipeline resumable, makes re-scoring free, and makes any past
ranking reproducible.

## Why the layers are ordered this way

The cascade exists for cost and for accuracy, in that order of obviousness but
the reverse order of importance.

**Cost:** 9 requirements × 128 resumes is 1,152 model calls done naively. Alias
matching resolves the majority for free; batching the remainder per candidate
brings it to roughly 128 calls.

**Accuracy:** the deterministic layer is *more* reliable than the model for the
cases it covers. `React.js` ≡ `React` is a lookup, not a judgement. Sending it
to a model introduces variance for no gain, and makes the result unexplainable —
an alias match can show the recruiter exactly which terms were searched.

The model is reserved for the thing only it can do: reading a paragraph and
deciding whether it constitutes evidence of a capability.

## Data flow for identity

```
resume ──► extraction ──► ┌─ candidates            (title, experience, profile)
                          │     ▲ worker reads and writes
                          │
                          └─ candidate_identities  (name, email, phone, photo)
                                ▲ worker has INSERT only, no SELECT
                                │
                          API reads, only when blind mode is off,
                          and writes an audit entry when it does
```

Scoring physically cannot read identity. Migration `0002_grants.py` enforces it
at the database level rather than relying on every future contributor
remembering.

## Frontend

- **React + TypeScript + Vite.** Routes are lazily loaded; vendor chunks split.
- **TanStack Query** owns server state. Nothing else caches API responses.
- **URL owns filter state** — a filtered results view is shareable and
  survives refresh.
- **Component state owns UI state** — drawers, modals, selection.
- `styles/tokens.css` is the ported prototype stylesheet, unchanged. Components
  wrap those classes rather than reimplementing them, which is why the
  production app looks identical to the approved design.

## Removed by design

Three things a resume-screening product is commonly asked for, and why they are
not here:

**Sentiment / tone analysis.** Resume tone tracks native language, cultural
writing conventions, and access to a resume coach. It does not track competence.
Scoring on it means scoring a protected-class proxy, and no renaming fixes that.

**Candidate photos in ranking views.** A photo injects race, approximate age,
gender presentation, and perceived attractiveness into a screening decision at
the exact moment attention is highest.

**Precise match percentages.** `87.3%` implies a calibrated probability the
system does not have. Coverage counts state exactly what is known — how many
requirements had supporting evidence — and nothing more.

## Scaling notes

The current design comfortably handles a few hundred candidates per screening on
one worker. Beyond that:

- **Workers scale horizontally.** `docker-compose.prod.yml` already runs two.
  Add more; arq distributes jobs.
- **Embeddings are the memory cost.** Each worker loads ~440 MB. Consider a
  dedicated embedding service if worker count grows.
- **pgvector HNSW** is created in `0001_initial.py`. It handles millions of
  chunks; tune `ef_search` at query time if recall drops.
- **The model is the latency floor.** Verification is batched per candidate and
  the JD context is prompt-cached across a batch. Increase
  `VERIFICATION_BATCH_SIZE` before adding workers.
