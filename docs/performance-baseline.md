# Performance baseline

Measured, not estimated. Every number here came from a k6 run whose command is
recorded alongside it. Where something has not been measured, it says so rather
than carrying a plausible-looking guess.

---

## Test environment

**This is a developer laptop, not a server.** Numbers below are a floor, not a
prediction of production behaviour.

| | |
|---|---|
| Machine | Intel i7-6600U, 2 cores / 4 threads, 15.6 GB RAM |
| OS | Windows 10 22H2, Docker Desktop, WSL 2 backend |
| Docker memory | 6 GB |
| Running concurrently | postgres, redis, minio, clamav, backend, worker, frontend |
| Load generator | k6 in a container **on the same host** — it competes for the same CPU |
| Dataset | Seeded demo org: 8 ranked candidates, 4 quarantined, ~9 requirements |
| Date | 16 August 2026 |

Two caveats that matter when reading these figures: the load generator shares
the CPU with the system under test, and the dataset is small. A screening with
500 candidates will behave differently on the results endpoint, which paginates
but still counts and groups across the batch.

---

## Reproducing

```powershell
docker run --rm -i `
  --network resumeiq_default `
  -v "${PWD}/load:/load" `
  -e BASE_URL="http://backend:8000" `
  -e EMAIL="alex.morgan@northwind.demo" `
  -e PASSWORD="demo-password-2025" `
  -e SCENARIO="ramp50" `
  grafana/k6 run /load/k6-load.js
```

Scenarios: `smoke`, `load10`, `load50`, `ramp50`, `ramp100`.

The script exercises the three endpoints a recruiter waits on — ranked results,
candidate evidence, dashboard — with think-time between them. It deliberately
excludes job creation and screening start: both make model calls, so loading
them would measure Anthropic's latency and spend real money.

---

## MEASURED

### Single user (`smoke`, 1 VU, 30s)

| Endpoint | median | p95 | p99 | max |
|---|---:|---:|---:|---:|
| Ranked results | 19 ms | 45 ms | 50 ms | 51 ms |
| Candidate evidence | 25 ms | 95 ms | 97 ms | 98 ms |
| Dashboard | 29 ms | 44 ms | 47 ms | 48 ms |

18 requests, 0% failed.

### 50 concurrent, `DATABASE_POOL_SIZE=10` (`ramp50`, 4 min)

| Endpoint | median | p95 | p99 | max |
|---|---:|---:|---:|---:|
| Ranked results | 23 ms | 188 ms | 1,300 ms | 5,277 ms |
| Candidate evidence | 34 ms | 442 ms | 3,170 ms | 5,918 ms |
| Dashboard | 33 ms | 508 ms | 2,405 ms | 5,503 ms |

3,870 requests, **0% failed**, 0 rate-limited.
Dashboard p95 crossed its 500 ms threshold.

### 50 concurrent, `DATABASE_POOL_SIZE=25` (`ramp50`, 4 min)

| Endpoint | median | p95 | p99 | max |
|---|---:|---:|---:|---:|
| Ranked results | 23 ms | 225 ms | 1,111 ms | 2,378 ms |
| Candidate evidence | 32 ms | **229 ms** | **1,539 ms** | **2,736 ms** |
| Dashboard | 33 ms | **276 ms** | **1,276 ms** | **2,222 ms** |

3,948 requests, **0% failed**, 0 rate-limited. **All thresholds passed.**

---

## What the numbers say

**Connection pool size was the binding constraint.** Raising it from 10 to 25
halved candidate p95, cut dashboard p95 by 45%, and more than halved worst-case
latency everywhere. One line of configuration.

The mechanism is visible in the shape of the data: medians barely moved between
1 user and 50 (19→23 ms), while the tail exploded. That is queueing, not
saturation — most requests execute quickly, a few wait for a free connection and
then execute quickly. A genuinely overloaded system raises the median too.

**Throughput is not the limit at this scale.** ~16 requests/second sustained
with zero errors, on two cores also running the load generator.

**The remaining ~1–1.5 s p99 is most likely CPU.** Seven containers plus k6 on
two cores. A 4-vCPU host without the load generator co-resident should improve
this materially, but that is a prediction — it has not been measured.

---

## Applied change

`.env`:

```bash
DATABASE_POOL_SIZE=25   # was 10
```

Worth sizing against Postgres itself before raising further: the default
`max_connections` is 100, and `pool_size + max_overflow` per backend process
multiplied by the number of processes must stay below it. At 25 + 20 overflow
across 4 gunicorn workers you would be at 180 — over the limit. Either raise
`max_connections` or use a pooler (PgBouncer) in front.

---

## Targets — NOT yet met or NOT yet measured

| Metric | Target | Status |
|---|---|---|
| API p95 (reads) | < 300 ms | **Met** at 50 VUs after the pool change |
| API p99 (reads) | < 1,000 ms | **Not met** — 1.1–1.5 s |
| 100 concurrent users | no errors | **Not measured** |
| 50-resume screening | < 10 min | **Not measured** |
| 100-resume screening | < 20 min | **Not measured** |
| Queue depth under load | visible | **Not measured** — no metric exists |
| Worker memory ceiling | — | **Not measured** |
| Sustained soak (1 hour) | no leak | **Not measured** |

---

## Not measured, and why it matters

**Screening throughput is the number a customer will actually ask about.** "How
long for 100 CVs?" is unanswered. It is bounded by model round-trips rather than
CPU, so it needs its own test with a stubbed AI client to separate your latency
from Anthropic's.

**Nothing above touches the write path.** Uploads, screening creation, and the
worker pipeline are untested under load. A recruiter uploading 100 files while
another runs a screening is a realistic pattern and completely unmeasured.

**No soak test.** Four minutes will not reveal a memory leak. The worker holds a
440 MB embedding model resident; whether that grows over hours is unknown.

---

## Next

1. `ramp100` — find where it actually breaks
2. Screening throughput with a stubbed AI client — 10, 50, 100 resumes
3. One-hour soak at 10 VUs, watching worker RSS
4. Re-run all of the above on the target production host, not a laptop

Until step 4, treat every number here as a lower bound.
