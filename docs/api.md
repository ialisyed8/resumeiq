# API reference

Base path `/api`. All responses JSON. Interactive docs at `/api/docs` outside
production.

## Conventions

**Auth.** Bearer token in `Authorization`, or the `access_token` HTTP-only
cookie. Access tokens last 15 minutes; call `/auth/refresh` to rotate.

**Errors** share one shape:

```json
{
  "error": {
    "code": "not_found",
    "message": "That candidate does not exist.",
    "request_id": "3f9a...",
    "fields": [{"field": "email", "message": "value is not a valid email"}]
  }
}
```

`request_id` matches the `X-Request-ID` response header and the `request_id` in
the server logs. Quote it in bug reports.

**Tenancy.** Every query is scoped to the caller's organization. There is no
cross-organization read path.

---

## Auth

### `POST /api/auth/register`
Creates an organization and its first (admin) user.

```bash
curl -X POST localhost:8000/api/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"full_name":"Alex Morgan","email":"alex@northwind.com",
       "password":"a-long-passphrase-here","company":"Northwind"}'
```

### `POST /api/auth/login`

```bash
curl -X POST localhost:8000/api/auth/login -c cookies.txt \
  -H 'Content-Type: application/json' \
  -d '{"email":"alex.morgan@northwind.demo","password":"demo-password-2025"}'
```

```json
{ "access_token": "eyJ...", "refresh_token": "eyJ...", "expires_in": 900,
  "user": {"id":"...","email":"...","full_name":"Alex Morgan","role":"admin"} }
```

Five failed attempts locks the account for 15 minutes.

### `POST /api/auth/refresh`
Rotates the refresh token. Reusing an already-rotated token revokes the whole
token family — that pattern indicates theft.

### `POST /api/auth/logout` · `POST /api/auth/forgot-password` · `POST /api/auth/reset-password` · `GET /api/auth/me`

`forgot-password` returns an identical response whether or not the address is
registered.

---

## Jobs and requirements

### `POST /api/jobs`
Creates a job and extracts requirements from the description.

```bash
curl -X POST localhost:8000/api/jobs -b cookies.txt \
  -H 'Content-Type: application/json' \
  -d '{"raw_text":"Senior Frontend Engineer\n\n5+ years React..."}'
```

```json
{ "id": "...", "title": "Senior Frontend Engineer",
  "requirements_extracted": 12, "ai_enabled": true,
  "message": "12 requirements extracted. Review them before screening." }
```

Without `ANTHROPIC_API_KEY` the job is still created; `requirements_extracted`
is 0 and requirements can be added manually.

### `GET /api/jobs` · `GET /api/jobs/{id}` · `DELETE /api/jobs/{id}`

### `GET /api/jobs/{id}/requirements`

```json
{ "items": [{
  "id": "...", "text": "React, 5+ years production", "kind": "skill",
  "necessity": "must_have", "weight": "High", "canonical_skill": "React",
  "aliases": ["React.js","ReactJS","React 18"], "min_years": 5,
  "recruiter_edited": false, "source_span": "Deep React expertise" }] }
```

### `POST /api/jobs/{id}/requirements` · `PATCH .../{rid}` · `DELETE .../{rid}`

Changing `necessity` re-tiers the entire candidate pool and is audited under its
own action (`requirement.necessity_changed`).

### `PATCH /api/jobs/{id}/weights`

```json
{"skills":35,"experience":30,"projects":20,"education":10,"certifications":5}
```

Weights reorder candidates within a coverage tier only.

---

## Uploads

### `POST /api/uploads/presign`
Presigned S3 PUT for large files. The storage key is generated server-side.

### `POST /api/jobs/{id}/candidates`
Multipart upload.

```bash
curl -X POST localhost:8000/api/jobs/$JOB/candidates -b cookies.txt \
  -F 'files=@resume1.pdf' -F 'files=@resume2.docx'
```

```json
{ "accepted": [{"candidate_id":"...","filename":"resume1.pdf","reference":"001"}],
  "rejected": [{"filename":"cv.exe","reason":".exe is not supported. Upload a PDF or DOCX."}],
  "duplicates": [],
  "summary": {"accepted":1,"rejected":1,"duplicates":0} }
```

Rejections are itemised with a reason. Nothing fails silently.

---

## Screening

### `POST /api/jobs/{id}/screenings` → `202 Accepted`

```json
{ "batch_id": "...", "status": "queued", "total_documents": 42,
  "events_url": "/api/screenings/.../events",
  "message": "Screening queued. This continues in the background." }
```

Rejects with 409 if no must-have requirement exists or no resumes are uploaded.

### `GET /api/screenings/{id}/events` — Server-Sent Events

```javascript
const es = new EventSource('/api/screenings/abc/events', { withCredentials: true })
es.addEventListener('progress', e => console.log(JSON.parse(e.data)))
es.addEventListener('completed', () => es.close())
```

```
event: progress
data: {"status":"verifying","processed":18,"total":42,"quarantined":2,"failed":0,"progress":43}
```

Requires `proxy_buffering off` at any intermediate proxy.

### `GET /api/screenings/{id}/results`

Query: `page`, `page_size`, `tier`, `decision_status`, `min_experience`,
`max_experience`, `q`, `sort` (`rank|score|experience|coverage`), `blind`.

```json
{ "items": [{
    "candidate_id":"...", "reference":"001", "display_name":"Candidate #001",
    "rank":1, "must_haves_met":9, "must_haves_total":9,
    "coverage_tier":"meets_all", "match_level":"Strong Match",
    "final_score":95.9, "missing_requirements":[], "decision_status":"new" }],
  "total":38, "tier_counts":{"meets_all":6,"one_short":11,"multiple_gaps":21},
  "blind_screening":true, "scorer_version":"2.1.0",
  "disclaimer":"Scores rank resumes against this job description only..." }
```

Passing `blind=false` writes an audit entry.

### `GET /api/screenings/{id}/quarantine`

Documents held out of the ranking, with the reason each could not be read.

### `POST /api/screenings/{id}/rescore`

```bash
curl -X POST localhost:8000/api/screenings/$B/rescore -b cookies.txt \
  -H 'Content-Type: application/json' \
  -d '{"skills":50,"experience":30,"projects":10,"education":5,"certifications":5}'
```

Reads stored evidence; opens no PDF and calls no model. Fast enough for a
slider.

### `GET /api/screenings` · `GET /api/screenings/{id}`

---

## Candidates

### `GET /api/screenings/{id}/candidates/{cid}`

Returns per-requirement coverage where every entry carries either a verbatim
`quote` with offsets or an `absence_statement`:

```json
{ "coverage": [
    { "text":"React, 5+ years production", "verdict":"met",
      "evidence_band":"direct_with_context", "evidence_grade":1.0,
      "quote":"Rebuilt the Stripe Checkout surface in React 18...",
      "page":1, "char_start":142, "char_end":198,
      "confidence_band":"high", "quote_validated":true, "method":"embedding_llm" },
    { "text":"Accessibility (WCAG 2.1 AA)", "verdict":"not_met",
      "evidence_band":"none", "evidence_grade":0.0, "quote":null,
      "absence_statement":"No mention of WCAG, ARIA, a11y, or screen reader found in the submitted resume.",
      "search_terms":["WCAG","ARIA","a11y","screen reader"] }],
  "score": {"must_haves_met":8,"must_haves_total":9,"coverage_tier":"one_short",
            "final_score":75.9,"rank":5,"scorer_version":"2.1.0",
            "tier_note":"This score orders candidates within the 'one requirement short' tier..."},
  "gaps":[...], "questions":[...], "decision_trail":[...] }
```

`blind=false` reveals identity and logs `candidate.identity_revealed`.

### `GET .../candidates/{cid}/document`
Extracted text plus a presigned download URL, for the highlight viewer.

### `POST .../candidates/{cid}/decision`

```json
{"action": "shortlist", "note": "Strong accessibility evidence."}
```

`action` ∈ `shortlist | reject | interview | on_hold | reset`. Recorded with
actor, timestamp, coverage at decision time, and scorer version.

### `PATCH .../candidates/{cid}/evidence/{rid}`

```json
{"verdict": "met", "reason": "Discussed in portfolio, missing from resume."}
```

The model's original verdict is retained alongside the override.

### `DELETE /api/candidates/{id}`
Erases documents and identity, soft-deletes the candidate so audit history stays
coherent.

---

## Reports

- `GET /api/dashboard` — KPIs, pool distribution, common gaps, recent screenings
- `GET /api/screenings/{id}/report` — summary, requirement breakdown, top 10
- `GET /api/screenings/{id}/export/csv` — respects the blind setting
- `GET /api/screenings/{id}/audit` — audit trail

## Health

- `GET /api/health` — liveness
- `GET /api/health/deep` — database, Redis, storage, and Anthropic reachability;
  503 when any dependency is down
