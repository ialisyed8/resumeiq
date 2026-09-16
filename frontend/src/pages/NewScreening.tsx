/**
 * The screening wizard.
 *
 * Step 2 (requirement review) is the load-bearing one. It converts the ranking
 * from "what the job description says" into "what the recruiter says the role
 * needs", and it is why every downstream explanation traces to a
 * human-approved criterion rather than to a model's reading of marketing copy.
 */

import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, subscribeToScreening } from '@/api/client'
import { Badge, Button, Card, EmptyState, Modal, SkeletonRows } from '@/components/ui'
import {
  useJob, useRequirementAdd, useRequirementDelete, useRequirementUpdate,
} from '@/hooks/queries'
import { useToast } from '@/hooks/useToast'
import type { CategoryWeights, Requirement } from '@/api/types'

const STEPS = ['Job', 'Requirements', 'Resumes', 'Screening', 'Results']
const DEFAULT_WEIGHTS: CategoryWeights = {
  skills: 35, experience: 30, projects: 20, education: 10, certifications: 5,
}

export default function NewScreening() {
  const [step, setStep] = useState(1)
  const [jobId, setJobId] = useState<string | null>(null)
  const [batchId, setBatchId] = useState<string | null>(null)

  return (
    <div className="wrap wrap-narrow page">
      <div className="steps">
        {STEPS.map((label, i) => {
          const n = i + 1
          const cls = step === n ? 'on' : step > n ? 'done' : ''
          return (
            <div key={label} style={{ display: 'contents' }}>
              <div className={`step ${cls}`}>
                <span className="step-n">{step > n ? '✓' : n}</span>
                <span className="step-l">{label}</span>
              </div>
              {i < STEPS.length - 1 && <span className="step-line" />}
            </div>
          )
        })}
      </div>

      {step === 1 && <StepJob onDone={(id) => { setJobId(id); setStep(2) }} />}
      {step === 2 && jobId && (
        <StepRequirements jobId={jobId} onBack={() => setStep(1)} onNext={() => setStep(3)} />
      )}
      {step === 3 && jobId && (
        <StepUpload jobId={jobId} onBack={() => setStep(2)}
          onStarted={(id) => { setBatchId(id); setStep(4) }} />
      )}
      {step === 4 && batchId && <StepProcessing batchId={batchId} />}
    </div>
  )
}

function StepJob({ onDone }: { onDone: (jobId: string) => void }) {
  const { toast } = useToast()
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)

  const analyse = async () => {
    setBusy(true)
    try {
      const result = await api.post<{ id: string; requirements_extracted: number; message: string }>(
        '/jobs', { raw_text: text },
      )
      toast(result.message)
      onDone(result.id)
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Could not analyse that job description.', 'bad')
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <div className="ph">
        <h1>Create screening</h1>
        <p className="ph-sub">
          Add the job description you want to screen candidates against. You'll
          review the extracted requirements before anything runs.
        </p>
      </div>
      <Card pad>
        <label className="field" style={{ marginBottom: 14 }}>
          <span className="field-label">Job description</span>
          <textarea
            className="textarea" rows={16} value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Paste the job description here — responsibilities, required skills, qualifications, and experience level."
          />
        </label>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 9 }}>
          <Button variant="pri" onClick={analyse} disabled={text.length < 40 || busy}>
            {busy ? 'Reading the job description…' : 'Analyze job description'}
          </Button>
        </div>
      </Card>
      <p style={{ fontSize: 12.5, color: 'var(--ink-3)', marginTop: 14, textAlign: 'center' }}>
        Paste the text directly. ResumeIQ does not connect to LinkedIn or Indeed.
      </p>
    </>
  )
}

function StepRequirements({ jobId, onBack, onNext }: {
  jobId: string; onBack: () => void; onNext: () => void
}) {
  const { data: job, isLoading } = useJob(jobId)
  const update = useRequirementUpdate(jobId)
  const remove = useRequirementDelete(jobId)
  const add = useRequirementAdd(jobId)
  const { toast } = useToast()

  const [weights, setWeights] = useState<CategoryWeights>(DEFAULT_WEIGHTS)
  const [adding, setAdding] = useState(false)
  const [newText, setNewText] = useState('')
  const [newNecessity, setNewNecessity] = useState<'must_have' | 'nice_to_have'>('must_have')

  useEffect(() => {
    if (job?.category_weights) setWeights(job.category_weights)
  }, [job?.category_weights])

  if (isLoading) return <Card><SkeletonRows count={6} /></Card>
  if (!job) return <EmptyState title="Job not found" />

  const musts = job.requirements.filter((r) => r.necessity === 'must_have')
  const nices = job.requirements.filter((r) => r.necessity === 'nice_to_have')

  const setNecessity = async (r: Requirement, necessity: string) => {
    if (r.necessity === necessity) return
    await update.mutateAsync({ requirementId: r.id, patch: { necessity } })
    toast(`${r.text} is now ${necessity === 'must_have' ? 'a must-have' : 'a nice-to-have'}.`)
  }

  const saveWeights = async () => {
    await api.patch(`/jobs/${jobId}/weights`, weights)
    toast('Weights saved.')
  }

  return (
    <>
      <div className="ph">
        <h1>Review requirements</h1>
        <p className="ph-sub">
          We extracted these from your job description. Change priority, delete
          anything that doesn't matter, or add a requirement the description
          didn't spell out. Candidates are scored against this list — not the raw text.
        </p>
      </div>

      <Card pad style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', gap: 13, flexWrap: 'wrap' }}>
          <Badge tone="blue">{job.title}</Badge>
          {job.min_years_experience && <Badge>{job.min_years_experience}+ years</Badge>}
          <Badge>{musts.length} must-have · {nices.length} nice-to-have</Badge>
        </div>
      </Card>

      <RequirementGroup
        title="Must-have" count={musts.length} items={musts}
        description="A candidate missing any of these stays in a lower coverage tier. Nice-to-haves cannot make up for them."
        onNecessity={setNecessity}
        onWeight={(r, weight) => update.mutate({ requirementId: r.id, patch: { weight } })}
        onDelete={(r) => { remove.mutate(r.id); toast(`Removed ${r.text}.`, 'warn') }}
        onAdd={() => { setNewNecessity('must_have'); setAdding(true) }}
      />
      <RequirementGroup
        title="Nice-to-have" count={nices.length} items={nices}
        description="These separate candidates who already meet every must-have."
        onNecessity={setNecessity}
        onWeight={(r, weight) => update.mutate({ requirementId: r.id, patch: { weight } })}
        onDelete={(r) => { remove.mutate(r.id); toast(`Removed ${r.text}.`, 'warn') }}
        onAdd={() => { setNewNecessity('nice_to_have'); setAdding(true) }}
      />

      <Card pad style={{ marginTop: 20 }}>
        <div className="rg-head" style={{ marginBottom: 4 }}>
          <div className="rg-title"><h4>Ranking weights</h4></div>
          <Button variant="ghost" size="sm" onClick={() => setWeights(DEFAULT_WEIGHTS)}>
            Reset to recommended
          </Button>
        </div>
        <p style={{ fontSize: 12.5, color: 'var(--ink-2)', marginBottom: 10 }}>
          These order candidates <strong>within</strong> the same coverage tier.
          They cannot lift a candidate over a missing must-have.
        </p>
        {(Object.keys(weights) as (keyof CategoryWeights)[]).map((key) => (
          <div className="wt-row" key={key}>
            <span className="wt-name" style={{ textTransform: 'capitalize' }}>{key}</span>
            <input type="range" min={0} max={60} value={weights[key]}
              aria-label={`${key} weight`}
              onChange={(e) => setWeights({ ...weights, [key]: Number(e.target.value) })} />
            <span className="wt-val num">{weights[key]}%</span>
          </div>
        ))}
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 12 }}>
          <Button size="sm" onClick={saveWeights}>Save weights</Button>
        </div>
      </Card>

      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 9, marginTop: 20 }}>
        <Button variant="ghost" onClick={onBack}>Back</Button>
        <Button variant="pri" onClick={onNext} disabled={musts.length === 0}>
          Continue to resumes
        </Button>
      </div>

      {adding && (
        <Modal
          title="Add requirement" onClose={() => setAdding(false)}
          footer={
            <>
              <Button variant="ghost" onClick={() => setAdding(false)}>Cancel</Button>
              <Button variant="pri" disabled={newText.length < 2}
                onClick={async () => {
                  await add.mutateAsync({ text: newText, necessity: newNecessity })
                  toast(`Added ${newText}.`)
                  setNewText(''); setAdding(false)
                }}>
                Add requirement
              </Button>
            </>
          }
        >
          <label className="field" style={{ marginBottom: 14 }}>
            <span className="field-label">Requirement</span>
            <input className="input" value={newText} onChange={(e) => setNewText(e.target.value)}
              placeholder="e.g. Has shipped a design system" />
          </label>
          <label className="field">
            <span className="field-label">Priority</span>
            <select className="select" value={newNecessity}
              onChange={(e) => setNewNecessity(e.target.value as never)}>
              <option value="must_have">Must-have</option>
              <option value="nice_to_have">Nice-to-have</option>
            </select>
          </label>
        </Modal>
      )}
    </>
  )
}

function RequirementGroup({ title, count, items, description, onNecessity, onWeight, onDelete, onAdd }: {
  title: string; count: number; items: Requirement[]; description: string
  onNecessity: (r: Requirement, n: string) => void
  onWeight: (r: Requirement, w: string) => void
  onDelete: (r: Requirement) => void
  onAdd: () => void
}) {
  return (
    <div className="req-group">
      <div className="rg-head">
        <div className="rg-title">
          <h4>{title}</h4>
          <Badge tone={title === 'Must-have' ? 'blue' : 'slate'}>{count}</Badge>
        </div>
        <Button size="sm" onClick={onAdd}>Add requirement</Button>
      </div>
      <p style={{ fontSize: 12.5, color: 'var(--ink-2)', marginBottom: 11 }}>{description}</p>
      {items.length === 0 ? (
        <Card pad><div style={{ textAlign: 'center', color: 'var(--ink-3)', fontSize: 13 }}>
          Nothing here yet. Add a requirement or move one across.
        </div></Card>
      ) : items.map((r) => (
        <div className="rq" key={r.id}>
          <div className="rq-main">
            <div className="rq-name">{r.text}</div>
            {r.aliases.length > 0 && (
              <div className="rq-alias">
                {r.aliases.slice(0, 6).map((a) => <span className="alias" key={a}>{a}</span>)}
              </div>
            )}
          </div>
          <div className="rq-ctl">
            <div className="seg" role="group" aria-label={`Priority for ${r.text}`}>
              <button data-k="must" className={r.necessity === 'must_have' ? 'on' : ''}
                onClick={() => onNecessity(r, 'must_have')}>Must-have</button>
              <button className={r.necessity === 'nice_to_have' ? 'on' : ''}
                onClick={() => onNecessity(r, 'nice_to_have')}>Nice-to-have</button>
            </div>
            <select className="select" style={{ width: 96, height: 31, fontSize: 12 }}
              value={r.weight} onChange={(e) => onWeight(r, e.target.value)}>
              <option>High</option><option>Medium</option><option>Low</option>
            </select>
            <button className="icon-btn" style={{ width: 31, height: 31, color: 'var(--ink-3)' }}
              onClick={() => onDelete(r)} aria-label={`Delete ${r.text}`}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
              </svg>
            </button>
          </div>
        </div>
      ))}
    </div>
  )
}

function StepUpload({ jobId, onBack, onStarted }: {
  jobId: string; onBack: () => void; onStarted: (batchId: string) => void
}) {
  const { toast } = useToast()
  const inputRef = useRef<HTMLInputElement>(null)
  const [uploaded, setUploaded] = useState<{ filename: string; ok: boolean; reason?: string }[]>([])
  const [busy, setBusy] = useState(false)
  const [over, setOver] = useState(false)

  const upload = async (selected: File[]) => {
    setBusy(true)
    const body = new FormData()
    selected.forEach((f) => body.append('files', f))
    try {
      const result = await api.post<{
        accepted: { filename: string }[]
        rejected: { filename: string; reason: string }[]
        duplicates: { filename: string; reason: string }[]
      }>(`/jobs/${jobId}/candidates`, body)

      setUploaded((current) => [
        ...current,
        ...result.accepted.map((a) => ({ filename: a.filename, ok: true })),
        ...result.rejected.map((r) => ({ filename: r.filename, ok: false, reason: r.reason })),
        ...result.duplicates.map((d) => ({ filename: d.filename, ok: false, reason: d.reason })),
      ])
      if (result.rejected.length) toast(`${result.rejected.length} file(s) could not be accepted.`, 'warn')
      if (result.accepted.length) toast(`${result.accepted.length} resume(s) ready to screen.`)
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Upload failed.', 'bad')
    } finally {
      setBusy(false)
    }
  }

  const start = async () => {
    setBusy(true)
    try {
      const result = await api.post<{ batch_id: string; message: string }>(
        `/jobs/${jobId}/screenings`, { blind_screening: true },
      )
      toast(result.message)
      onStarted(result.batch_id)
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Could not start screening.', 'bad')
    } finally {
      setBusy(false)
    }
  }

  const accepted = uploaded.filter((u) => u.ok).length

  return (
    <>
      <div className="ph">
        <h1>Upload resumes</h1>
        <p className="ph-sub">
          PDF, DOC, and DOCX up to 10 MB each. Anything that can't be read is
          held for review rather than ranked low.
        </p>
      </div>
      <Card pad>
        <div className={`drop ${over ? 'over' : ''}`}
          onClick={() => inputRef.current?.click()}
          onDragOver={(e) => { e.preventDefault(); setOver(true) }}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => {
            e.preventDefault(); setOver(false)
            upload(Array.from(e.dataTransfer.files))
          }}>
          <div className="drop-ico">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v13" />
            </svg>
          </div>
          <h4>Drop resumes here or browse your device</h4>
          <p>Multiple files supported</p>
          <div className="fmt">PDF · DOC · DOCX · max 10 MB</div>
        </div>
        <input ref={inputRef} type="file" multiple hidden
          accept=".pdf,.doc,.docx,.odt,.rtf"
          onChange={(e) => e.target.files && upload(Array.from(e.target.files))} />

        {uploaded.length > 0 && (
          <div style={{ marginTop: 20 }}>
            {uploaded.map((f, i) => (
              <div className={`file ${f.ok ? '' : 'err'}`} key={`${f.filename}-${i}`}>
                <div className="file-main">
                  <div className="file-name">{f.filename}</div>
                  <div className="file-meta" style={{ color: f.ok ? undefined : 'var(--red)' }}>
                    {f.ok ? 'Ready to screen' : f.reason}
                  </div>
                </div>
                {f.ok && <Badge tone="green">Ready</Badge>}
              </div>
            ))}
          </div>
        )}
      </Card>

      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 9, marginTop: 20 }}>
        <Button variant="ghost" onClick={onBack}>Back to requirements</Button>
        <Button variant="pri" disabled={!accepted || busy} onClick={start}>
          Screen {accepted} resume{accepted === 1 ? '' : 's'}
        </Button>
      </div>
    </>
  )
}

const PIPELINE = [
  'Document upload', 'Text extraction', 'Requirement extraction',
  'Evidence matching', 'Candidate scoring', 'Final ranking',
]

function StepProcessing({ batchId }: { batchId: string }) {
  const navigate = useNavigate()
  const [progress, setProgress] = useState({
    processed: 0, total: 0, quarantined: 0, failed: 0, progress: 0, status: 'queued',
  })

  useEffect(() => {
    const unsubscribe = subscribeToScreening(batchId, {
      onProgress: setProgress,
      onComplete: () => navigate(`/screenings/${batchId}`, { replace: true }),
      onFailed: (data) => setProgress({ ...data, status: 'failed' }),
    })
    return unsubscribe
  }, [batchId, navigate])

  const stageIndex = Math.min(5, Math.floor((progress.progress / 100) * 6))

  return (
    <div className="proc">
      <h2>Screening candidates</h2>
      <p className="proc-sub">
        This runs in the background. You can close this tab and come back —
        we'll email you when it finishes.
      </p>
      <div className="proc-n">
        {progress.processed}<span className="den"> / {progress.total}</span>
      </div>
      <div className="proc-pct">{progress.progress}% complete</div>
      <div className="proc-bar">
        <div className="proc-fill" style={{ width: `${progress.progress}%` }} />
      </div>
      <div className="pipe">
        {PIPELINE.map((label, i) => (
          <div className={`pipe-step ${i < stageIndex ? 'done' : i === stageIndex ? 'now' : 'wait'}`} key={label}>
            <span className="pipe-ico">
              {i < stageIndex && (
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round">
                  <path d="M20 6L9 17l-5-5" />
                </svg>
              )}
            </span>
            <span className="pipe-lab">{label}</span>
          </div>
        ))}
      </div>
      <div className="proc-stats">
        <Stat value={progress.processed} label="Processed" color="var(--green)" />
        <Stat value={progress.quarantined} label="Need review" color="var(--amber)" />
        <Stat value={progress.failed} label="Failed" color="var(--red)" />
        <Stat value={progress.total - progress.processed} label="Remaining" color="var(--ink-2)" />
      </div>
      <Button variant="ghost" size="sm" style={{ marginTop: 22 }} onClick={() => navigate('/')}>
        Leave this running and go to dashboard
      </Button>
    </div>
  )
}

function Stat({ value, label, color }: { value: number; label: string; color: string }) {
  return (
    <div className="pstat">
      <div className="pstat-v" style={{ color }}>{value}</div>
      <div className="pstat-l">{label}</div>
    </div>
  )
}
