# ResumeIQ — Private Beta Readiness Report

**Date:** 15 August 2026
**Phase:** Production hardening → pilot readiness
**Starting point:** 51/100, "Ready for private testing"

---

## Scope honesty — read first

This environment has **no Docker daemon, no PostgreSQL, no ClamAV daemon, and no
browser.** That bounds what could be verified, and it changes the verdict.

| Work | Status |
|---|---|
| Code implemented | Yes |
| Pure-Python tests executed | Yes — 411 passing |
| Integration tests executed | **No.** 35 tests exist and skip; they have never run against a live database. |
| Backup restore performed | **No.** Scripts written, never executed. |
| ClamAV verified against live daemon | **No.** Protocol tested with a fake; live test written and skipped. |
| E2E (Playwright) | **No.** Not run. |
| Load testing | **No.** No performance number in this report is measured. |

Anything below marked **UNVERIFIED** means the code exists and the test exists,
but neither has executed in a real environment. That distinction is the whole
point of this report.

---

## 1. Changes implemented this session

An initial inspection showed substantially more hardening already in place than
the previous audit recorded — `core/limits.py`, `services/email.py`,
`services/retention.py`, a regression suite, endpoint-protection tests, a CI
pipeline, and `docker-compose.test.yml` all existed. **The previous audit's P0-1
finding ("rate limiting is dead code") was stale**: rate limiting had been
re-implemented under tiered dependencies (`ReadUser` / `WriteUser` /
`UploadUser` / `AiUser`), and the grep that produced the finding searched for the
old function name. Correcting that was the first act of this session.

Rather than rebuild working systems, this session closed the three genuine gaps.

### Malware scanning (new)

`app/services/malware.py` — clamd INSTREAM over TCP, wired into the upload
pipeline between validation and storage.

The design decision that matters is **what happens when the scanner is down**.
Fail-open keeps uploads working and silently removes a security control exactly
when something might be exploiting the outage; fail-closed refuses uploads for a
problem that is not the user's fault. The default is **fail-closed in
production, fail-open in development**, overridable by `CLAMAV_FAIL_OPEN`, and
choosing fail-open logs loudly so the decision appears in the record.

A confirmed detection is rejected **regardless** of the fail-open setting.

- Infected files are never written to object storage and never reach the worker
- Detection writes `resume.rejected_malware` to the audit log with the
  **signature name only** — never file content
- Error messages do not leak scanner internals (tested)
- `clamav` sidecar added to both compose files; ~1 GB signature DB on a volume
- Wired into `/api/health/deep`

### Documentation (new)

- `docs/disaster-recovery.md` — RPO 24h / RTO 4h with basis, four disaster
  scenarios, post-restore verification checklist, testing schedule, and an
  explicit **"current status: no verified restore has ever been performed"**
- `docs/privacy-data-flow.md` — every stage from upload to results: what is
  stored, who can read it, retention, deletion, and which third party sees it

### Deliberately not built

**Demographic capture.** The previous audit recommended it for bias auditing.
Collecting protected-characteristic data carries its own legal obligations, and
building it because an audit mentioned it would be the wrong reason. Flagged in
`docs/privacy-data-flow.md` as a legal/product decision.

---

## 2. Files changed

| File | Change |
|---|---|
| `app/services/malware.py` | **New.** clamd client, scan policy, health probe |
| `app/api/v1/uploads.py` | Scan stage between validation and storage |
| `app/services/audit.py` | `UPLOAD_REJECTED_MALWARE` action |
| `app/core/config.py` | `CLAMAV_*` settings, `clamav_fail_open` property |
| `app/main.py` | Scanner in `/api/health/deep` |
| `docker-compose.yml`, `docker-compose.prod.yml` | `clamav` service + volume |
| `.env.example` | ClamAV configuration documented |
| `tests/security/test_malware.py` | **New.** 18 passing, 2 skipped (live daemon) |
| `docs/disaster-recovery.md` | **New** |
| `docs/privacy-data-flow.md` | **New** |

---

## 3. Test results — exact counts

```
411 passed, 44 skipped
```

| Suite | Passed | Failed | Skipped | Notes |
|---|---:|---:|---:|---|
| unit | 196 | 0 | 0 | |
| security | 152 | 0 | 9 | 2 skips are the live-clamd tests |
| eval | 14 | 0 | 0 | Ranking quality on the fixture pool |
| regression | 49 | 0 | 0 | AI behaviour |
| **integration** | **0** | **0** | **35** | **Never executed — no database** |
| frontend (vitest) | — | — | — | Not run this session |

### Differentiator regression — all six intact

95 tests covering the guarantees that must not break:

| Guarantee | Verified |
|---|---|
| Gate-first ranking | Tier remains primary sort key across extreme weight vectors |
| Evidence grounding | Ungrounded quotes downgraded to no-evidence |
| Blind screening | Worker grant model tested |
| Hedge cap | "familiar with" capped at 0.2 regardless of model output |
| Append-only audit | No update/delete path exposed |
| Quality gate | Unreadable documents quarantined, not scored low |

---

## 4. Security status

| Issue | Severity | Status |
|---|---|---|
| Rate limiting not enforced | Critical | **Resolved** — tiered dependencies; audit finding was stale |
| Org AI budget absent | Critical | **Resolved** — pre-flight check, bounded-overshoot race documented |
| Malware scanning absent | High | **Implemented, UNVERIFIED against live daemon** |
| Candidate text in logs | High | **Resolved** — `quote_preview` removed |
| Audit endpoint ignored `batch_id` | Medium | **Resolved** |
| Candidate/batch scoping | Medium | **Resolved** — `_scoped` binds candidate to batch's job |
| Password reset non-functional | High | **Resolved** — email abstraction, UNVERIFIED against a live provider |
| Dependency scanning | Medium | **Resolved** — `pip-audit` + Trivy in CI |
| Backups | Critical | **Scripts written, NO VERIFIED RESTORE** |
| Storage encryption in production | Medium | Configurable, **off by default** |
| Least-privilege DB roles | Medium | Migration exists; **requires separate `DATABASE_URL`s per service to take effect** |

---

## 5. Performance

**Nothing in this section is measured.** No load test has been run against this
system, in this session or any previous one.

| Metric | Target | Measured |
|---|---|---|
| API p95 | < 300 ms | **Unknown** |
| 50-resume screening | < 10 min | **Unknown** |
| Concurrent users | 50 | **Unknown** |
| Queue depth under load | — | **Unknown** |

The only data point is anecdotal: 4 resumes took ~175 seconds on a 2-core
laptop after model warm-up. Extrapolating that to 50 resumes suggests ~35
minutes, which would miss the target — but extrapolation is not measurement, and
batching behaviour is non-linear.

---

## 6. Remaining risks

**Not hidden, in order of severity.**

1. **No verified backup restore.** Until `restore.sh` has run against a real
   dump and passed the checklist, treat this system as having no backups. One
   hour of work; the highest-value hour available.
2. **Integration tests have never executed.** 35 tests, 35 endpoints, zero runs.
   Every bug found during first-boot debugging lived in exactly this layer.
3. **ClamAV never tested against a live daemon.** Protocol framing is tested
   with a fake. If INSTREAM framing is wrong, uploads fail closed in
   production — safe, but a hard outage.
4. **No performance data at all.**
5. **No AI evaluation on real data.** The fixture pool is synthetic and
   self-authored. Ranking accuracy remains unmeasured.
6. **Email provider unverified.** Password reset is implemented and untested
   end to end.
7. **Legal artefacts absent** — privacy policy, ToS, DPA, candidate
   notification, bias audit.
8. **Budget race allows bounded overshoot** under concurrency. Documented in
   `limits.py`; closing it costs serialisation.

---

## 7. Deployment requirements

**New environment variables:**

```bash
CLAMAV_ENABLED=true
CLAMAV_HOST=clamav
CLAMAV_PORT=3310
CLAMAV_TIMEOUT_SECONDS=30
CLAMAV_FAIL_OPEN=            # blank = fail closed in production

BACKUP_S3_BUCKET=            # required for off-host backups
BACKUP_RETENTION_DAYS=30
ALERT_WEBHOOK=               # backup failure notification

S3_SERVER_SIDE_ENCRYPTION=AES256   # must be set in production
```

**New services:** `clamav` (~1 GB signature DB, ~3 min to first ready scan).

**Cron:**
```
0 2 * * * infrastructure/scripts/backup.sh
0 3 1 * * infrastructure/scripts/backup.sh --verify
```

**Migrations:** none new.
**Rebuild:** required (compose change). ~10 min with cache intact.

---

## 8. Rollback

Nothing in this session changed the schema, so rollback is a redeploy.

```bash
git checkout <previous-tag>
docker compose -f docker-compose.prod.yml up -d --build
```

**To disable malware scanning without a rollback** — if the scanner proves
unstable and you accept the risk:

```bash
CLAMAV_ENABLED=false     # skips scanning entirely
# or
CLAMAV_FAIL_OPEN=true    # scans, accepts on scanner failure
```

Both are logged. Neither disables rejection of a confirmed detection.

---

## 9. Verdict

# READY FOR PRIVATE BETA
### Not yet ready for a company pilot.

**Estimated: 68/100**, up from 51.

### Why not "company pilot"

Your own Definition of Done requires:

- ✗ **Integration tests execute successfully** — 35 skipped, never run
- ✗ **Backup restore has been tested** — never performed
- ✗ **Critical E2E flow passes** — not run
- ✗ **Malware scanning implemented** — implemented, unverified against a real daemon

Four unmet criteria, and none can be met from this environment. Claiming pilot
readiness would mean claiming verification I did not perform.

**"Private beta"** is the honest ceiling: safe for you, or a colleague, to use
with data you control. The distinction from "company pilot" is not pedantic — a
pilot means another organisation's candidates' personal data, and that requires
a tested restore and executed cross-tenant tests, not merely written ones.

---

## 10. Next five actions, in order

**1. Run the integration suite.** (~2 hours)
```powershell
docker compose -f docker-compose.test.yml up -d
$env:TEST_DATABASE_URL="postgresql+asyncpg://resumeiq:resumeiq@localhost:5433/resumeiq_test"
pytest tests/integration -v
```
35 tests, 35 endpoints, cross-tenant isolation. Expect failures — that is the
point. This is where every previous bug lived.

**2. Perform a verified restore.** (~1 hour)
```powershell
docker compose exec backend /app/../infrastructure/scripts/backup.sh --verify
```
Until this passes, you do not have backups.

**3. Verify ClamAV against the live daemon.** (~30 min)
```powershell
docker compose up -d clamav   # wait ~3 min for signatures
pytest tests/security/test_malware.py -k live_clamd --no-skip
```
Then upload the EICAR string through the real UI and confirm rejection.

**4. Run the E2E journey.** (~3 hours)
Register → login → job → upload → screen → results → candidate → decision →
audit → logout. Plus failure paths.

**5. Load test.** (~3 hours)
k6 at 10/50/100 concurrent plus one 100-resume batch. Replace every "Unknown"
in §5 with a measurement. Publish to `docs/performance-baseline.md`.

**After those five, you are at a defensible company-pilot posture** — assuming
they pass, and assuming the legal artefacts exist by then.

---

## Closing

The expensive-to-retrofit things remain right: gate-first ranking, quote
grounding, blind screening enforced by database grant, an append-only audit
trail, and the continued refusal to score sentiment or show photographs. This
session did not weaken any of them, and 95 regression tests prove it.

What separates this from pilot-ready is not more code. It is **running the tests
that already exist** in an environment that has a database, a scanner, and a
browser. That work is measured in hours, not weeks — but it cannot be skipped,
and it cannot be done from here.
