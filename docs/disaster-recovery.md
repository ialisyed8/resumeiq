# Disaster recovery

The audit trail is the reason this document exists. ResumeIQ's value proposition
is that a hiring decision can be explained six months later — which is only true
if the evidence behind it survives. A backup that has never been restored is a
hypothesis, not a recovery plan.

---

## Objectives

| | Target | Basis |
|---|---|---|
| **RPO** (max data loss) | **24 hours** | Nightly `pg_dump`. A screening run after the last backup would be lost and must be re-run. |
| **RTO** (max downtime) | **4 hours** | Time to provision Postgres, restore a dump, redeploy, and verify. Measured on a scratch restore, not estimated. |

These are pilot-appropriate, not enterprise-grade. If a pilot customer needs an
RPO under an hour, move to a managed Postgres with continuous WAL archiving
(RDS/Cloud SQL point-in-time recovery). That is a configuration change, not a
code change.

### What is *not* covered by RPO

| Data | Recoverable? |
|---|---|
| Postgres (screenings, evidence, scores, audit, users) | Yes, to last nightly backup |
| Object storage (resume PDFs) | **Only if bucket versioning + replication is on.** Enable it. |
| Redis (queue state) | No, and it does not need to be. An interrupted screening is re-runnable; nothing of record lives in Redis. |
| Embedding model cache | No. Re-downloads automatically (~440 MB, one time). |

---

## What is backed up

`infrastructure/scripts/backup.sh`, run nightly:

- `pg_dump` of the entire database, gzip-compressed
- Uploaded to `BACKUP_S3_BUCKET`
- 30-day retention (`BACKUP_RETENTION_DAYS`)
- Failure POSTs to `ALERT_WEBHOOK` — deliberately terse, carries no connection string

```bash
# crontab, 02:00 UTC daily
0 2 * * * /opt/resumeiq/infrastructure/scripts/backup.sh >> /var/log/resumeiq-backup.log 2>&1

# monthly verified restore — the one that actually matters
0 3 1 * * /opt/resumeiq/infrastructure/scripts/backup.sh --verify >> /var/log/resumeiq-backup.log 2>&1
```

### Encryption

Backups contain candidate PII — names, emails, phone numbers, full resume text.
They must be encrypted at rest.

- **S3:** enable default bucket encryption (SSE-S3 or SSE-KMS). Set
  `S3_SERVER_SIDE_ENCRYPTION=AES256` for the application's own writes too.
- **Local staging directory:** on an encrypted volume, or delete immediately
  after upload (the script uses a `mktemp` dir with a cleanup trap).
- **Access:** the backup bucket should be readable only by the backup role and
  whoever performs restores. It is a full copy of every candidate you have ever
  screened.

---

## Restore procedure

`infrastructure/scripts/restore.sh` requires an explicit confirmation phrase,
because it destroys the target database.

```bash
# 1. Retrieve
aws s3 cp s3://$BACKUP_S3_BUCKET/resumeiq-20260815T020000Z.sql.gz /tmp/

# 2. Restore to a SCRATCH database first — never straight to production
createdb resumeiq_restore_test
./infrastructure/scripts/restore.sh /tmp/resumeiq-20260815T020000Z.sql.gz \
  postgresql://user:pass@host:5432/resumeiq_restore_test

# 3. Verify (see checklist below)

# 4. Only then, if this is a real recovery, restore to production
```

### Post-restore verification checklist

Do not declare recovery complete until all of these pass.

```sql
-- Row counts against the pre-incident baseline
SELECT 'organizations' t, count(*) FROM organizations
UNION ALL SELECT 'users', count(*) FROM users
UNION ALL SELECT 'candidates', count(*) FROM candidates
UNION ALL SELECT 'requirement_evidence', count(*) FROM requirement_evidence
UNION ALL SELECT 'candidate_scores', count(*) FROM candidate_scores
UNION ALL SELECT 'audit_logs', count(*) FROM audit_logs;

-- pgvector survived (dumps lose the extension if not restored in order)
SELECT count(*) FROM resume_chunks WHERE embedding IS NOT NULL;

-- Migrations are at head
SELECT version_num FROM alembic_version;
```

Then, through the application:

- [ ] Log in as a known user
- [ ] Open a historical screening — ranks and scores match what they were
- [ ] Open a candidate — evidence quotes are present
- [ ] Check `scorer_version` on old scores is unchanged
- [ ] `GET /api/health/deep` returns all green
- [ ] Re-score a batch — result is identical to the stored ranking

That last check is the meaningful one: it proves evidence survived intact, not
just row counts.

---

## Scenarios

### 1. Database corruption or accidental deletion

**Detection:** health check fails, or queries error.
**RTO ~2h.**

1. Stop the backend and worker so nothing writes to a damaged database
2. Restore the most recent backup to a scratch database, verify
3. Promote: point `DATABASE_URL` at the restored database, or restore over the original
4. `alembic upgrade head`
5. Restart services, run the verification checklist
6. **Tell affected customers what window of work was lost** — screenings run
   after the last backup must be re-run

### 2. Object storage loss (resume PDFs gone)

**The database survives**, so rankings, evidence, and audit records are intact —
but the source documents are gone, and "Open source" will fail.

Without bucket versioning this is unrecoverable. **Enable versioning and
cross-region replication before a pilot.** Candidates would have to re-submit.

### 3. Complete host loss

**RTO ~4h.**

1. Provision a new host, install Docker
2. Clone the repository, restore `.env` from your secrets manager
3. `docker compose -f docker-compose.prod.yml up -d`
4. Restore the database
5. Point storage at the surviving bucket
6. Update DNS
7. Full verification checklist

### 4. Ransomware / compromise

Do **not** restore straight into the compromised environment.

1. Isolate the host; preserve it for investigation
2. **Rotate every credential**: `SECRET_KEY`, `JWT_SECRET`, database password,
   S3 keys, `ANTHROPIC_API_KEY`
3. Build a clean host from source
4. Restore from a backup taken *before* the earliest evidence of compromise
5. Force logout of all users (rotating `JWT_SECRET` does this)
6. Review `audit_logs` for the compromise window — it is append-only, so it is
   the most trustworthy record you have
7. Legal/notification obligations apply. Involve counsel.

---

## Testing schedule

| Frequency | Test | Success |
|---|---|---|
| Daily | Backup completes | Object present in bucket, non-zero, alert silent |
| **Monthly** | **`backup.sh --verify`** | Restores to scratch DB, row counts match |
| Quarterly | Full DR drill from a clean host | RTO met, checklist passes |
| After schema change | Verified restore | Dump restores under the new schema |

**Record every drill** — date, who ran it, actual RTO, what went wrong. A drill
that surfaces nothing usually means it was not realistic enough.

---

## Current status — be honest about this

| Item | Status |
|---|---|
| Backup script | Written, **not yet scheduled on any host** |
| Restore script | Written, **never executed against a real dump** |
| Off-host copy | Requires `BACKUP_S3_BUCKET`, **not yet configured** |
| Failure alerting | Requires `ALERT_WEBHOOK`, **not yet configured** |
| Verified restore | **Never performed** |
| Bucket versioning | **Not enabled** |

**Until a restore has actually been performed and verified, treat this system as
having no backups at all.** The scripts are necessary and not sufficient. This is
a blocker for a company pilot — running it is a one-hour task and it is the
highest-value hour available before a pilot starts.
