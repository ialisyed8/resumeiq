# Deployment

## Before you start

This system processes resumes, which are personal data, and informs hiring
decisions, which are regulated. Two things to settle before production:

1. **Legal review.** Automated employment decision tools are covered by NYC
   Local Law 144, the EU AI Act, Illinois HB 3773, and Colorado SB 24-205 among
   others. `docs/security.md` summarises the landscape; your counsel decides
   what applies.
2. **Bias monitoring from day one.** A bias audit needs historical data you
   cannot generate retroactively. The audit log is built for this. Store
   self-reported demographics in a separate table the scoring pipeline cannot
   read.

## Requirements

| | Minimum | Comfortable |
|---|---|---|
| Backend | 2 vCPU, 2 GB | 4 vCPU, 4 GB |
| Worker | 2 vCPU, 4 GB | 4 vCPU, 8 GB (embedding model is ~440 MB resident) |
| Postgres 16 + pgvector | 2 vCPU, 4 GB, 50 GB | managed instance |
| Redis 7 | 1 GB | managed instance |
| Object storage | S3 or compatible | versioning + SSE enabled |

## Environment

```bash
cp .env.example .env
python3 -c "import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(48))"
python3 -c "import secrets; print('JWT_SECRET=' + secrets.token_urlsafe(48))"
```

Set at minimum:

```bash
APP_ENV=production
DEBUG=false
LOG_FORMAT=json
COOKIE_SECURE=true
CORS_ORIGINS=https://resumeiq.yourcompany.com     # no wildcard

SECRET_KEY=<generated>
JWT_SECRET=<generated>

DATABASE_URL=postgresql+asyncpg://user:pass@db-host:5432/resumeiq
REDIS_URL=redis://:password@redis-host:6379/0

S3_ENDPOINT=                    # empty for AWS S3
S3_ACCESS_KEY=<iam key>
S3_SECRET_KEY=<iam secret>
S3_BUCKET=resumeiq-prod-documents
S3_REGION=eu-west-1
S3_USE_SSL=true

ANTHROPIC_API_KEY=sk-ant-...
```

**Startup will fail deliberately** if `SECRET_KEY` is short, CORS contains `*`,
`DEBUG` is true, `COOKIE_SECURE` is false, or the MinIO default credentials are
still present. That check lives in `app/core/config.py`.

Prefer a secrets manager over a `.env` file in production. Every setting reads
from the process environment, so injecting them works without code changes.

## TLS

```bash
mkdir -p infrastructure/nginx/certs
# Let's Encrypt
certbot certonly --standalone -d resumeiq.yourcompany.com
cp /etc/letsencrypt/live/resumeiq.yourcompany.com/fullchain.pem infrastructure/nginx/certs/
cp /etc/letsencrypt/live/resumeiq.yourcompany.com/privkey.pem  infrastructure/nginx/certs/
```

Set `server_name` in `infrastructure/nginx/nginx.conf`.

## Deploy

```bash
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml exec backend alembic upgrade head
curl -fsS https://resumeiq.yourcompany.com/api/health/deep | jq
```

Expect `{"status":"ok"}` with database, redis, storage, and anthropic all
reporting healthy. Do **not** run `python -m app.seed` in production — it
creates demo records.

### Database roles (recommended)

Migration `0002_grants.py` creates `resumeiq_app` and `resumeiq_worker` and
revokes the worker's read access to `candidate_identities`. To actually get that
protection, connect each service as its own role:

```sql
CREATE USER resumeiq_api    WITH PASSWORD '...' IN ROLE resumeiq_app;
CREATE USER resumeiq_worker_u WITH PASSWORD '...' IN ROLE resumeiq_worker;
```

Then give the backend and worker containers different `DATABASE_URL` values.
Running both as the owner works, but discards a real safeguard.

## Verifying the deployment

```bash
# 1. Health
curl -fsS https://host/api/health/deep | jq

# 2. Security headers
curl -sI https://host/ | grep -Ei 'strict-transport|content-security|x-frame'

# 3. SSE is not buffered — this must stream, not block
curl -N -H "Authorization: Bearer $TOKEN" https://host/api/screenings/$ID/events

# 4. Upload rejection works
printf 'MZ\x90\x00' > fake.pdf
curl -X POST https://host/api/jobs/$JOB/candidates -F 'files=@fake.pdf' -b cookies.txt
# expect: rejected with a reason

# 5. No secrets in the image
docker run --rm --entrypoint sh resumeiq-backend -c 'env | grep -i anthropic' # empty
```

## Operations

### Backups

```bash
# Nightly
pg_dump "$DATABASE_URL" | gzip > resumeiq-$(date +%F).sql.gz
aws s3 sync s3://resumeiq-prod-documents s3://resumeiq-backups/documents/
```

Test restores. An untested backup is a hypothesis.

### Scaling

```bash
docker compose -f docker-compose.prod.yml up -d --scale worker=4
```

Workers are stateless and coordinate through Redis. Before adding workers,
raise `VERIFICATION_BATCH_SIZE` — it usually helps more, because throughput is
bounded by model round-trips rather than CPU.

### Monitoring

Watch:

- `GET /api/health/deep` — dependency status
- Queue depth in Redis — sustained growth means workers are undersized
- `screening.failed` audit entries
- `security.injection_detected` audit entries
- Quarantine rate — a sudden rise usually means a new resume format is
  defeating the parser, not that candidates got worse

Logs are JSON with `request_id` on every line and `job_id` on worker lines. Ship
to your aggregator and index both.

### Upgrades

```bash
git pull
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml exec backend alembic upgrade head
docker compose -f docker-compose.prod.yml up -d
```

If `SCORER_VERSION` changed, existing screenings keep their original scores.
Re-score deliberately, and expect ranks to move — that is the version doing its
job.

### Data retention

`DATA_RETENTION_DAYS` (default 180) governs resumes and extracted data;
`AUDIT_RETENTION_DAYS` (default 2555) governs the audit trail. Schedule a
retention job against these values; the deletion path is
`DELETE /api/candidates/{id}`, which erases documents and identity while keeping
audit history coherent.

## Rollback

```bash
git checkout <previous-tag>
docker compose -f docker-compose.prod.yml up -d --build
# only if the release included a schema change:
docker compose -f docker-compose.prod.yml exec backend alembic downgrade -1
```

Check the migration's `downgrade()` before running it. Some are lossy by nature.
