# Privacy data flow

What happens to a candidate's data at every stage: where it is stored, who can
read it, how long it lives, and which third parties see it.

**This is engineering documentation, not legal advice.** It describes what the
system does so that counsel can assess what it means. Statements about GDPR,
LL144, or the EU AI Act belong in a legal review, not here.

---

## Overview

```
  Recruiter uploads resume
            │
            ▼
  ┌─────────────────────┐
  │ 1. Validation       │  extension, magic bytes, size, zip bomb
  │    (API process)    │  rejected → nothing stored, reason returned
  └──────────┬──────────┘
             ▼
  ┌─────────────────────┐
  │ 2. Malware scan     │  clamd, in-memory, no persistence
  │    (clamav sidecar) │  infected → nothing stored, audited
  └──────────┬──────────┘
             ▼
  ┌─────────────────────┐
  │ 3. Object storage   │  original PDF/DOCX, encrypted at rest
  │    (S3 / MinIO)     │  randomised key, not derived from filename
  └──────────┬──────────┘
             ▼
  ┌─────────────────────┐
  │ 4. Text extraction  │  PyMuPDF / python-docx / Tesseract — all local
  │    (worker)         │  no third party sees the document here
  └──────────┬──────────┘
             │
             ├──────────────► 5a. Contact extraction (regex, local)
             │                    └─► candidate_identities
             │                        worker: INSERT only, no SELECT
             │
             ▼
  ┌─────────────────────┐
  │ 5b. Chunk + embed   │  bge-base-en-v1.5, runs locally, no API call
  │     (worker)        │  └─► resume_chunks (text + vector)
  └──────────┬──────────┘
             ▼
  ┌─────────────────────┐
  │ 6. AI processing    │  ⚠ THIRD PARTY: Anthropic API
  │    (worker)         │  sends: resume text (sanitised), requirements
  │                     │  never sends: name, email, phone, address
  └──────────┬──────────┘
             ▼
  ┌─────────────────────┐
  │ 7. Evidence + score │  requirement_evidence, candidate_scores
  │    (worker)         │  quotes, offsets, verdicts, versioned scores
  └──────────┬──────────┘
             ▼
  ┌─────────────────────┐
  │ 8. Results          │  identity attached only when blind mode is off,
  │    (API process)    │  and that reveal is audited
  └─────────────────────┘
```

---

## Stage detail

### 1. Upload validation — API process

| | |
|---|---|
| **Data handled** | Raw file bytes, original filename, declared MIME |
| **Persisted** | Nothing yet |
| **Access** | API process only, in memory |
| **Third parties** | None |
| **On rejection** | Nothing stored. Filename and reason are returned to the uploader and recorded in the audit log; **file contents are not logged**. |

The uploaded filename is never used as a storage path (`build_key()` generates a
random key), so a malicious filename cannot direct where data lands.

### 2. Malware scan — clamav sidecar

| | |
|---|---|
| **Data handled** | File bytes streamed to clamd over a local socket |
| **Persisted** | Nothing. clamd scans in memory. |
| **Access** | Container-local network only; the scanner is not exposed |
| **Third parties** | None. Signature DB is downloaded; documents are never uploaded anywhere. |
| **On detection** | File is **not** stored. Audit records filename and signature name — never content. |

### 3. Object storage — S3 / MinIO

| | |
|---|---|
| **Data stored** | The original document, unmodified. Contains full PII. |
| **Key** | `orgs/{org_id}/resumes/{date}/{random}.pdf` — tenant-scoped, unguessable |
| **Encryption** | `S3_SERVER_SIDE_ENCRYPTION` — **must be set in production** |
| **Access** | Backend and worker service credentials. Recruiters get time-limited presigned URLs (15 min default). |
| **Retention** | `DATA_RETENTION_DAYS` (default 180) |
| **Deletion** | `DELETE /api/candidates/{id}` removes the object immediately |

### 4. Text extraction — worker, local

| | |
|---|---|
| **Data handled** | Full document text |
| **Persisted** | `resume_documents.extracted_text` |
| **Third parties** | **None.** PyMuPDF, python-docx, Tesseract, LibreOffice all run in-container. |

Hidden text (white-on-white, sub-4pt) is detected and **excluded** from what is
screened, and flagged on the document record.

### 5a. Contact extraction — worker, local, regex only

| | |
|---|---|
| **Data extracted** | Name, email, phone, profile links |
| **Persisted** | `candidate_identities` |
| **Method** | Regex and heuristics. **Deliberately not a model call.** |
| **Access** | Worker role has `INSERT` but **no `SELECT`** (migration `0002_grants.py`). API role can read it; doing so through the UI is audited. |

This is the central privacy control. Identity lives in a separate table with
separate grants, so the scoring pipeline physically cannot read it — enforced by
database permission, not developer discipline. Using regex rather than the model
means identity never enters the AI path at all.

### 5b. Chunking and embeddings — worker, local

| | |
|---|---|
| **Data stored** | `resume_chunks`: text segments, section labels, char offsets, 768-dim vectors |
| **Model** | `bge-base-en-v1.5`, downloaded once, **runs locally** |
| **Third parties** | None. No embedding API is used. |

### 6. AI processing — **third party: Anthropic**

The only stage where candidate data leaves your infrastructure.

| | |
|---|---|
| **Sent** | Sanitised resume text (or retrieved chunks), requirement list, job title |
| **Never sent** | Name, email, phone, address, photograph — the extraction prompt explicitly refuses to return them, and contact details are extracted separately by regex |
| **Purpose** | Structured extraction, evidence verification, screening questions |
| **Provider** | Anthropic. Review their data retention and training terms; configure zero-retention if your agreement supports it. |
| **Disable** | Unset `ANTHROPIC_API_KEY` — the app runs on stored evidence with no external calls |

Before the send, `sanitise_for_prompt()` strips invisible characters and
neutralises delimiter sequences.

**For a DPA:** Anthropic is a sub-processor. Name them explicitly, and state the
region their API is called from.

### 7. Evidence and scores

| | |
|---|---|
| **Stored** | Verdict, grade, **quoted resume text**, char offsets, page, confidence, method, model version |
| **Note** | `requirement_evidence.evidence_quote` contains verbatim resume excerpts — this table holds PII |
| **Purpose** | Explainability and reproducibility. This is what allows a decision to be justified later. |
| **Retention** | Follows candidate retention |

### 8. Results and reveal

| | |
|---|---|
| **Default** | Blind. Responses carry `Candidate #001`, no identity fields. |
| **Reveal** | Requires `blind=false`; writes `candidate.identity_revealed` to the audit log |
| **Exports** | CSV and PDF are anonymous by default. The identified variants are separate deliberate actions, audited, and the PDF stamps a handling warning on every page. |

---

## Who can access what

| Role | Candidate identity | Resume text | Evidence | Audit |
|---|---|---|---|---|
| Recruiter (blind on) | No | Via quotes | Yes | Own org |
| Recruiter (blind off) | Yes, audited | Yes | Yes | Own org |
| Org admin | Yes, audited | Yes | Yes | Own org |
| **Worker process** | **No — DB grant denies SELECT** | Yes | Yes | Insert only |
| Other organisations | **No** — every query is org-scoped | No | No | No |
| Anthropic | **No** | Yes, sanitised | n/a | No |

---

## Retention and deletion

| Data | Default | Setting |
|---|---|---|
| Resume documents | 180 days | `DATA_RETENTION_DAYS` |
| Extracted text, chunks, evidence | 180 days | same |
| Audit records | 7 years | `AUDIT_RETENTION_DAYS` |
| Refresh tokens | 14 days | `REFRESH_TOKEN_TTL_DAYS` |

Enforcement runs in `app/services/retention.py`.

**Deletion** (`DELETE /api/candidates/{id}`):

- Object storage document — **removed**
- `candidate_identities` row — **removed**
- `resume_documents`, `resume_chunks` — **removed**
- Candidate row — **soft-deleted**, PII fields cleared
- Evidence and scores — retained, no longer identifiable
- Audit record — **retained**, records that a deletion occurred

The audit trail deliberately survives. It records that a decision was made and
later erased, without retaining who it was about.

---

## Data minimisation — what is deliberately never collected

These are excluded by design, and the extraction prompt refuses to return them:

| Excluded | Because |
|---|---|
| Photographs | Race, age, gender presentation |
| Date of birth, graduation year | Age |
| Institution name | Socioeconomic status, race |
| Address | Race, socioeconomic status |
| Nationality, visa status | National origin |
| Marital/family status | Sex, caregiving |
| Employment gaps | Measured for context, **never scored** |
| Writing tone / sentiment | Native language, class, culture |

**There is no sentiment analysis in this system and there must not be.** It is
not a feature that becomes acceptable when renamed.

---

## Gaps — required before a pilot

| Gap | Owner |
|---|---|
| Privacy policy, ToS, DPA | Legal |
| Candidate notification mechanism | Legal + product |
| Demographic capture for bias auditing | **Legal/product decision, not an engineering one.** Do not build until reviewed — collecting protected-characteristic data has its own obligations. |
| Data export (portability) | Engineering |
| Anthropic sub-processor disclosure | Legal |
| Bucket versioning for document recovery | Engineering |
| Storage encryption enabled in production | Engineering — set `S3_SERVER_SIDE_ENCRYPTION` |

---

## For a customer DPA

Facts an enterprise customer will ask for:

- **Sub-processors:** Anthropic (AI processing), your cloud provider (hosting,
  storage). No analytics or tracking SDKs are present.
- **Data location:** wherever you deploy; Anthropic API region is a separate
  question to answer explicitly.
- **Encryption:** TLS 1.2+ in transit; at rest via cloud provider — must be
  switched on.
- **Retention:** configurable per organisation; default 180 days.
- **Deletion:** supported, immediate, and audited.
- **Breach detection:** audit log is append-only and enforced by grant.
- **Automated decision-making:** the system ranks and explains; **it never
  rejects.** Every shortlist and rejection is a recorded human action. This
  distinction matters for GDPR Art. 22 and is worth stating plainly.
