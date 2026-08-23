# ResumeIQ

A resume screening system that ranks candidates against a job description and
shows its working — every verdict traces to a quoted line from the resume, and
every gap states what was searched for and not found.

Built to explore a specific question: **can an AI screening tool be made
defensible enough that a recruiter could justify a rejection to the candidate?**

Most of what follows is about where it failed and what that taught me. The stack
is at the bottom, where it belongs.

---

## The bug that mattered most

Two days before I stopped work on this, I was reading through a candidate report
and found this:

> **CI/CD security, vulnerability management, and incident response**
> *No evidence found* — "No mention of CI/CD security, vulnerability management,
> incident response, or security incident handling found in the submitted resume."

Two lines below it, on the same page, the system quoted her resume as evidence
for a different requirement:

> *"Cloud security engineer with 5+ years... partnering with DevOps teams on
> identity, logging, **vulnerability management, and incident response**."*

The system asserted a phrase was absent from a document, then quoted the phrase
from that document. Its own report disproved it.

### Why it happened

The verifier only ever sees *retrieved chunks*, not the whole resume. So when it
reports "not found", the honest reading is "not found in what I was shown". The
code rendered that as "no mention in the submitted resume", which claims far
more than the system knows.

### Why it took four attempts to fix

Absence claims were emitted from four independent places, and each fix looked
correct until a real document took a different route:

| # | Path | Why it was missed |
|---|---|---|
| 1 | `ground_verdict` | The obvious one — LLM output handling |
| 2 | `_no_evidence` | Fires when retrieval returns nothing; bypasses layer 1 entirely |
| 3 | `deterministic_pass` | The alias layer. Checked a *curated skill dictionary*, never the requirement's own extracted aliases — then returned `confidence=0.9` on a claim it had no basis for. Its comment read "genuinely absent across every alias" and was wrong. |
| 4 | The database column | The UI renders `absence_statement`, which regenerated the claim from scratch and discarded whatever the grader had reasoned |

Path 3 is the one I think about. Confidently wrong is worse than uncertain, and
the comment made the error look considered.

The fix now reports three distinct outcomes where there was one:

- **Genuinely absent** → "No mention of X, Y, or Z found in the submitted resume"
- **Present but undescribed** → "Mentioned (vulnerability management, incident
  response) but with no supporting description of the work"
- **Citation unverifiable** → "The supporting quote could not be verified against
  the document"

Each is true of what the system actually knows. Tests assert the property at all
four entry points rather than in one implementation, so a fifth path cannot
reintroduce it quietly.

---

## Three other findings worth reading

### A cheaper model silently destroyed the evidence layer

To conserve API budget I switched evidence verification from Sonnet to Haiku.
A well-qualified candidate went from **4/5 to 0/5**.

The mechanism: every positive verdict must cite text that is then fuzzy-matched
against the source at 0.88 similarity. Haiku *paraphrases* evidence; Sonnet
quotes it. All five citations scored 0.40–0.68 and were correctly rejected as
ungrounded.

The guard worked exactly as designed. The problem is that the system then
reported five confident absences and completed normally. A recruiter would have
rejected a qualified candidate and seen nothing amiss.

**The model choice is not a free cost lever, and it does not degrade
gracefully — it collapses.** A batch where most verdicts fail grounding should
fail loudly rather than produce hollow results.

### Requirement structure drives accuracy more than the model does

Same system, same day, two job descriptions:

| | Recall of "strong" in top 10 |
|---|---|
| **JD01** — Cloud Security Engineer | **11%** |
| **JD02** — Full-Stack Engineer | **89%** |

The difference was one requirement. JD01 contained:

> *CI/CD security, vulnerability management, and incident response*

Three separate disciplines in one requirement. It is satisfied by mentioning
*any* of them convincingly, so it rewards breadth over depth — the exact inverse
of the judgement a recruiter wants. Eight of nine strong candidates failed on it
while every medium candidate passed by name-checking all three.

JD02 had seven atomic requirements and near-perfect band separation.

**Accuracy work belongs upstream, in how requirements are structured, not
downstream in ranking weights.** Weights only reorder candidates *within* a
coverage tier; all the error was *at* the tier boundary, where weights cannot
reach.

### Sampling temperature was left at its default

The same job description produced 7, then 5, then 8 requirements across three
runs — one of them invented from the *Responsibilities* section rather than the
requirements. `temperature` had never been set, so it defaulted to 1.0.

A ranking someone has to defend cannot rest on a die roll. Set to 0.

---

## The design decision the whole thing rests on

**Coverage is a gate, not a score.**

A candidate missing a must-have stays in a lower tier no matter how strong they
are elsewhere. Ranking weights reorder candidates *within* a tier and can never
lift one across a boundary.

This is enforced structurally, and 13 tests attack it with extreme weight
vectors trying to break it. It is the property that makes the output explicable:
"ranked third because he has eight of nine requirements" is a sentence a
recruiter can say out loud. "Ranked third, 79% match" is not.

Other decisions that follow from the same principle:

**Evidence is grounded or it is discarded.** A positive verdict whose quote
cannot be located in the source is downgraded to no-evidence, not accepted with
a caveat. Fabricated evidence is worse than none because it looks convincing.

**Hedged language is capped.** "Familiar with Kubernetes" cannot exceed 0.2
regardless of what the model returns. Tested against a resume listing every
keyword in the job description with no described work — it scored **1/9**.

**Identity is separated by database grant, not convention.** The worker role has
`INSERT` but no `SELECT` on `candidate_identities`. The scoring pipeline
physically cannot read a candidate's name. Contact details are extracted by
regex, never by a model, so identity never enters the AI path at all.

**Unreadable documents are quarantined, not scored low.** A scanned PDF with no
text layer is held out of the ranking with an explanation, rather than ranked
last for reasons that look like a judgement about the person.

**No sentiment analysis, no photographs, no institution names, no graduation
dates.** Each is a protected-characteristic proxy. Employment gaps are measured
for context and never scored.

---

## What is verified, and what is not

I have tried to keep these separate throughout.

### Verified

| | |
|---|---|
| Tenant isolation | 18 endpoints, all denied cross-org access including candidate PII, resume documents, and reports |
| Backup restore | Dump → restore to scratch DB → row counts match across 6 tables |
| Malware scanning | ClamAV against a live daemon; EICAR detected, signature logged, file content not logged |
| Rate limiting | Fires at exactly the configured limit under real HTTP traffic |
| Load | 50 concurrent, 3,948 requests, 0% failures. p95 225ms after fixing a connection-pool bottleneck found by measurement (worst case 5.9s → 2.7s) |
| Tests | 421 passing, 44 skipped |

### Not verified

**Accuracy on real resumes.** Everything above used synthetic resumes generated
to fit their labels, which I labelled myself. That measures whether the pipeline
discriminates. It is not an accuracy claim and I have not made one.

Measuring accuracy properly needs two recruiters independently bucketing real
resumes against real job descriptions, with inter-rater agreement measured
first — because where two humans disagree is the ceiling no system beats. The
protocol is in `docs/ranking.md`.

**End-to-end browser testing, and a soak test.** Neither run.

---

## Why there is no live deployment

Processing real candidate data requires a privacy policy, terms of service, and
a Data Processing Agreement. None exist. Running a public instance where anyone
can upload a resume would mean handling strangers' personal data with no lawful
basis — and this is employment decision-making, which sits under GDPR Art. 22,
NYC Local Law 144, and the EU AI Act.

`docs/privacy-data-flow.md` traces what is stored at every stage, who can read
it, and which third party sees it. It exists so a lawyer can assess it.

The system is designed for it — retention enforcement, audited deletion,
append-only audit trail, blind screening by default — but designed-for and
cleared-for are different things.

---

## Running it

```bash
git clone <repo> && cd resumeiq
cp .env.example .env          # add ANTHROPIC_API_KEY, generate two secrets
docker compose up --build     # ~15 min first run
docker compose exec backend python -m app.seed
```

http://localhost:5173 — `alex.morgan@northwind.demo` / `demo-password-2025`

Seeded with 8 ranked candidates and 4 quarantined documents. No model calls
needed to explore it.

---

## Architecture

Modular monolith with a worker queue. Deliberately not microservices — nothing
about this workload justifies the operational cost.

**Matching cascade**, cheapest layer first:

1. **Alias matching** — deterministic, free, resolves most requirements outright
2. **BM25** — lexical relevance
3. **Embeddings** — `bge-base-en-v1.5`, runs locally, no embedding API
4. **LLM adjudication** — Claude, only where the cheap layers leave real ambiguity

The constraint that matters: layers 1–3 can only ever *propose* evidence. Only
layer 4 issues a verdict, and every verdict must quote text that is then
verified against the source.

**Stack:** FastAPI · PostgreSQL + pgvector · Redis + arq · React + TypeScript ·
MinIO/S3 · ClamAV · Docker

**Docs:** [`ranking.md`](docs/ranking.md) · [`security.md`](docs/security.md) ·
[`privacy-data-flow.md`](docs/privacy-data-flow.md) ·
[`performance-baseline.md`](docs/performance-baseline.md) ·
[`disaster-recovery.md`](docs/disaster-recovery.md)

---

## What I would do next

1. **Compound-requirement detection** — warn at extraction when a requirement
   bundles separate disciplines, and let the recruiter decide whether to split.
   Highest-value accuracy work available, and it needs no model.
2. **A real evaluation** — two recruiters, thirty resumes, agreement measured
   before anything else.
3. **Fail loudly on grounding collapse** — a batch where most citations fail
   validation should error, not complete.
4. **Legal review** before any real data touches it.

---

*Job descriptions and resumes used in testing are synthetic, created for
evaluation. No real candidate data was processed.*
