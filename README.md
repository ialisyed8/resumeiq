# AutoGuard AI

Autonomous incident diagnosis and remediation, built to run on a single
free-tier ARM64 VM under a $0/month infrastructure constraint, with a four-zone
trust model that keeps the reasoning tier structurally incapable of touching
production.

**Status: Phase 0, step P0.1 complete.** The repository skeleton exists and can
already reject architecture violations. No application logic yet — see
[Current state](#current-state).

---

## The one thing to understand first

The reasoning tier — the part that calls an LLM — has **no** production
execution privilege, directly or indirectly. Not because it is instructed not
to, but because:

- it runs as its own container image that does not contain a Kubernetes client,
  a cloud SDK, or the deployment adapter;
- its ServiceAccount has zero role bindings, so a leaked token authorises
  nothing;
- its network policy denies egress to every namespace, to the Kubernetes API and
  to the instance metadata endpoint, leaving only DNS, an allowlist of inference
  hosts, Postgres and one queue;
- its Postgres role can `INSERT` into one table and `SELECT` from one view, and
  cannot `UPDATE` or `DELETE` anything, anywhere;
- **zones do not call each other.** There is no endpoint in Zone 3 or Zone 4 for
  it to reach, forge a request to, or confuse.

The worst outcome from a fully compromised reasoning tier — including one whose
prompt has been hijacked by content in a customer's log stream — is a proposal
that fails deterministic verification, policy evaluation and human approval.

Prompt-injection defense here is architectural, not a prompt technique.

---

## Trust zones

| Zone | What it is | Runtime home | Credentials it holds |
|---|---|---|---|
| **1 — Untrusted** | A classification applied to *data*: logs, alerts, telemetry, repository content, user text, and all model output before validation | `svc-api`, `svc-ingest` in `ag-edge` (trusted code, untrusted input) | none for the data; scoped ingest tokens for the receiver |
| **2 — Reasoning** | Proposes; never acts | `worker-diagnosis` in `ag-reason` | inference API keys only |
| **3 — Verification** | Deterministic, independent of the model | `worker-verifier` in `ag-verify`, sandbox Jobs in `ag-sandbox` | none in the sandbox; a narrow database role in the verifier |
| **4 — Execution** | Sole mutation authority | `worker-deployer` in `ag-execute` | target-environment credentials, held nowhere else |

Handoff between zones is a Postgres row plus a queue message containing only an
opaque identifier. Zone 4 treats that identifier as a hint with no authority and
re-derives every authorization fact — plan digest, cryptographically signed
approval, approver role, separation of duty, verification result, policy
decision, safety state, artifact signature — before it acts. Every gate defaults
to denial; an exception raised anywhere in the sequence is a denial, not a retry.

Full detail: [`docs/architecture/`](docs/architecture/) and the Phase 0
Implementation Readiness Correction.

---

## Repository layout

```
packages/
  domain/            entities, invariants, state machines, ports — no I/O
  contracts/         schemas for everything crossing B1/B2/B3
  platform/          config validation, redaction, logging, safe HTTP, IDs
  application/       use cases, authorization, transaction boundaries
  adapters-data/     Postgres, Valkey, outbox queue, object storage
  adapters-llm/      Groq, Gemini, Ollama, mock, provider router
  adapters-deploy/   Zone 4 mutation — installed in exactly one image
apps/
  api/               Zone 1 exposed control plane
  web/               Next.js frontend
services/
  worker_diagnosis/  Zone 2
  worker_verifier/   Zone 3
  worker_deployer/   Zone 4
  scheduler/         outbox sweeper, retention, safety-state evaluation
db/ policy/ infra/ tests/ docs/
```

Adapters are split into three separate distributions on purpose. It means the
reasoning image's dependency resolution *cannot* pull in a Kubernetes client and
the deployer's *cannot* pull in an inference SDK — zone separation as a property
of the lockfile rather than of developer discipline. See
[ADR-0001](docs/adr/0001-monorepo-layout.md).

---

## Quickstart

Requires Python 3.12, [uv](https://docs.astral.sh/uv/), and Node 20+ for the
frontend.

```bash
make install     # workspace + dev tooling
make hooks       # git hooks (secret scanning, architecture contracts)
make check       # lint, types, architecture contracts, tests
```

`make help` lists every target. CI calls the same targets, so "passes locally"
and "passes in CI" mean the same thing.

### What `make check` does not cover

Three gates cannot run on a developer machine, and a green `make check` does not
mean the trust boundaries are enforced:

| Gate | Why it needs more than a laptop |
|---|---|
| ARM64 runtime gate | verifies images **start**, import every compiled dependency, and run their tests on native arm64. A successful `buildx` proves nothing about whether the binary runs. |
| Cluster security suite | zone attestation, secret scoping, sandbox contract and B3 forgery tests need a real cluster with NetworkPolicy and admission control. |
| Integration suite | role privileges, RLS and outbox recovery need real Postgres and Valkey. |

Docker Compose has no NetworkPolicy, no Pod Security admission and no
ValidatingAdmissionPolicy. It reproduces the container, network and credential
separation faithfully — including per-zone Postgres roles, so `ag_reason`
genuinely cannot write `approvals` on a laptop — but the three controls above
are verified only against a real cluster in CI. Treating Compose as proof of
isolation would be exactly the mistake this design exists to avoid.

---

## Security invariants

These hold at every commit. Each is enforced at runtime and tested, not merely
documented.

1. Postgres is authoritative. Valkey is disposable: flushing it entirely loses no
   work, because the sweeper rebuilds the queue from `work_items`.
2. Zone 2 holds no credential capable of mutating anything.
3. Only `worker-deployer` holds target-environment credentials, and it listens on
   no port.
4. Approval is a signed, digest-bound record verified by Zone 4 — never a
   boolean, never a client-supplied field. A plan edited after approval fails the
   digest check.
5. The sandbox is airgapped: no network, no DNS, no service-account token, no
   secrets, hard resource limits, one run per pod.
6. The Docker socket is never mounted anywhere.
7. Every production mutation is preceded by a committed audit event. If the audit
   write fails, the mutation does not happen.
8. Every gate fails closed. Missing input, timeout and exception all mean deny.
9. `audit_events` and `approvals` reject `UPDATE` and `DELETE` at the database.
10. No default path requires a payment method on file.

---

## Current state

Step **P0.1 — repository skeleton and enforcement scaffolding** is complete:
workspace and package manifests, architecture contracts with negative controls
proving they fire, hooks, ignore rules, the single-entry-point Makefile, and the
eight decision records.

Every package currently contains only its `__init__` and a docstring naming the
step that populates it. This is deliberate: the enforcement scaffolding is built
before the code it constrains, so no module is ever written against an
unenforced boundary.

Next: **P0.2 — platform and configuration**, starting with fail-closed startup
validation, so that nothing can start misconfigured before anything exists to
misconfigure.

---

## Decision records

| ADR | Decision |
|---|---|
| [0001](docs/adr/0001-monorepo-layout.md) | Monorepo split by trust zone, not by technology |
| [0002](docs/adr/0002-runtime-zone-isolation.md) | Zones are runtime deployables; build-time rules are never sufficient |
| [0003](docs/adr/0003-transactional-outbox-queue.md) | Postgres owns the work list; the queue is a signal |
| [0004](docs/adr/0004-valkey-over-redis.md) | Valkey for the self-hosted cache |
| [0005](docs/adr/0005-opentofu-over-terraform.md) | OpenTofu for infrastructure as code |
| [0006](docs/adr/0006-flux-pull-based-delivery.md) | Pull-based GitOps; no inbound path to Zone 4 |
| [0007](docs/adr/0007-sandbox-runtime-baseline.md) | Hardened-runc baseline mandatory; gVisor additive |
| [0008](docs/adr/0008-local-object-storage-default.md) | Local capped object storage by default |
