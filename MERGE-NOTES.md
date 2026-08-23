# Read this before replacing your working copy

This archive is **my copy** of ResumeIQ, not a merge of yours. It contains every
fix from our sessions, and 421 backend tests pass against it.

It does **not** contain changes you made that I never saw. The clearest evidence
of divergence: your running stack has services named `api`, `web`, and `db` on
ports 3000 and 5433. This archive has `backend`, `frontend`, and `postgres` on
8000 and 5432. Something restructured your compose file, and whatever else that
change brought with it is not here.

**Do not delete your existing directory and unzip this over the top.** You would
lose work silently.

## Reconcile with git instead

You have a repository with commits, which is the safest way through this.

```powershell
cd C:\dev\resumeiq
git status          # what is modified but uncommitted
git log --oneline   # your commit history
git stash           # park uncommitted changes if you want a clean slate
```

Then compare this archive against your tree file by file, and take what you
need. The files most likely to matter are listed below.

## What is current in this archive

Fixed during our last sessions, in the order they were found:

| File | Change |
|---|---|
| `backend/app/matching/cascade.py` | Absence claims verified against the full document at all three grading paths |
| `backend/app/workers/pipeline.py` | Display column carries the grader's actual reasoning; `_no_evidence` checks the document |
| `backend/app/ai/client.py` | `temperature=0.0` — extraction was non-deterministic |
| `backend/app/ai/prompts.py` | Ignore Responsibilities sections; drop overlapping requirements |
| `backend/app/services/malware.py` | ClamAV scanning, fail-closed in production |
| `backend/app/models/__init__.py` | `text("email")` index fix; enum `values_callable` wrapper |
| `backend/app/api/v1/reports.py` | CSV export honours blind screening; audit filters by batch |
| `backend/app/api/v1/candidates.py` | Per-candidate PDF report; `_scoped` binds candidate to batch |
| `backend/app/services/report_pdf.py` | PDF report generator |
| `backend/tests/` | 421 passing, including regression tests for every absence path |
| `frontend/src/vite-env.d.ts` | Types for `import.meta.env` — the production build failed without it |
| `frontend/src/components/ui.tsx` | Modal focus fix; `Card` accepts `style` |
| `load/k6-load.js` | Load test |
| `docs/` | performance-baseline, disaster-recovery, privacy-data-flow |

## What is NOT in here

- `.env` — regenerate secrets, never commit it
- `infrastructure/scripts/backup.sh` and `restore.sh` — you have these locally
- Anything from your compose restructure
- `node_modules`, `__pycache__`, build output

## Before pushing to GitHub

```powershell
git check-ignore -v .env backups/     # both must match a rule
git log --all --oneline -- .env       # must return nothing
```

If `.env` ever entered history, removing it needs `git filter-repo` — a plain
delete is not enough once it is in a commit.

Also rotate the Anthropic key. It has been visible in screenshots during our
sessions, and it will be in a public repository's blast radius if anything slips.
