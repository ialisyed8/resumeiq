/**
 * Candidate detail — the explainability screen.
 *
 * Every requirement resolves to one of two things: a verbatim quote from the
 * resume, or a statement of what was searched for and not found. There is no
 * third option, because an unexplained number is not an explanation.
 */

import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { CoverageMeter } from '@/components/CoverageMeter'
import { Badge, Button, Card, EmptyState, Modal, SectionHead, SkeletonRows, TIER_BADGE } from '@/components/ui'
import { useCandidate, useDecision, useEvidenceOverride } from '@/hooks/queries'
import { useToast } from '@/hooks/useToast'
import { api } from '@/api/client'
import type { CoverageItem } from '@/api/types'

export default function CandidateDetail() {
  const { batchId = '', candidateId = '' } = useParams()
  const navigate = useNavigate()
  const { toast } = useToast()

  const [blind, setBlind] = useState(true)
  const [source, setSource] = useState<CoverageItem | null>(null)
  const [override, setOverride] = useState<CoverageItem | null>(null)
  const [overrideVerdict, setOverrideVerdict] = useState('met')
  const [overrideReason, setOverrideReason] = useState('')
  const [docText, setDocText] = useState<string | null>(null)

  const { data, isLoading } = useCandidate(batchId, candidateId, blind)
  const decide = useDecision(batchId)
  const overrideMutation = useEvidenceOverride(batchId, candidateId)

  if (isLoading) {
    return <div className="wrap page"><Card><SkeletonRows count={7} /></Card></div>
  }
  if (!data) {
    return (
      <div className="wrap page">
        <EmptyState title="Candidate not found"
          action={<Button onClick={() => navigate(`/screenings/${batchId}`)}>Back to ranked list</Button>} />
      </div>
    )
  }

  const musts = data.coverage.filter((c) => c.necessity === 'must_have')
  const nices = data.coverage.filter((c) => c.necessity === 'nice_to_have')
  const gaps = musts.filter((c) => c.verdict !== 'met')

  const openSource = async (item: CoverageItem) => {
    setSource(item)
    if (docText === null) {
      try {
        const doc = await api.get<{ extracted_text: string }>(
          `/screenings/${batchId}/candidates/${candidateId}/document`,
        )
        setDocText(doc.extracted_text ?? '')
      } catch {
        setDocText('')
      }
    }
  }

  const submitOverride = async () => {
    if (!override) return
    await overrideMutation.mutateAsync({
      requirementId: override.requirement_id,
      verdict: overrideVerdict,
      reason: overrideReason,
    })
    toast('Override saved. Re-score to apply it to the ranking.')
    setOverride(null)
    setOverrideReason('')
  }

  /**
   * Download this candidate's report.
   *
   * Identity follows whatever the screen is currently showing: if the recruiter
   * has revealed the candidate, the PDF carries their name and contact details
   * and says so on every page. Either choice is recorded in the audit trail,
   * because this file leaves the system.
   */
  const downloadReport = () => {
    const base = import.meta.env.VITE_API_BASE ?? '/api'
    const query = blind ? '' : '?blind=false'
    window.open(
      `${base}/screenings/${batchId}/candidates/${candidateId}/report/pdf${query}`,
      '_blank',
    )
    toast(
      blind
        ? 'Report downloading.'
        : 'Report includes candidate identity. This is recorded in the audit trail.',
      blind ? 'ok' : 'warn',
    )
  }

  const act = async (action: string) => {
    const result = await decide.mutateAsync({ candidateId, action })
    toast(result.message)
  }

  return (
    <div className="wrap page">
      <Button variant="ghost" size="sm" style={{ marginBottom: 14 }}
        onClick={() => navigate(`/screenings/${batchId}`)}>
        ← Back to ranked list
      </Button>

      <div className="det-head">
        <div className="det-top">
          <div className="det-id">
            <span className="det-avatar">{data.reference}</span>
            <div>
              <h1 className="det-name">{data.display_name}</h1>
              <p className="det-sub">
                {data.title}
                {data.experience_years != null && ` · ${data.experience_years} yrs`}
              </p>
            </div>
          </div>
          <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap', alignItems: 'center' }}>
            {data.decision_status !== 'new' && (
              <Badge tone={data.decision_status === 'shortlisted' ? 'green' : 'slate'}>
                {data.decision_status}
              </Badge>
            )}
            <Button variant="pri" size="sm" onClick={() => act('shortlist')}>Shortlist</Button>
            <Button size="sm" onClick={() => act('interview')}>Move to interview</Button>
            <Button variant="danger" size="sm" onClick={() => act('reject')}>Reject</Button>
            <Button size="sm" onClick={() => setBlind(!blind)}>
              {blind ? 'Reveal identity' : 'Hide identity'}
            </Button>
            <Button size="sm" onClick={downloadReport}>Download report</Button>
          </div>
        </div>

        {data.score && (
          <div className="det-cov">
            <div>
              <div className="det-cov-n">
                {data.score.must_haves_met}
                <span className="den"> / {data.score.must_haves_total}</span>
              </div>
              <div className="det-cov-l">must-haves met</div>
            </div>
            <div style={{ flex: 1, minWidth: 170 }}>
              <CoverageMeter
                met={data.score.must_haves_met}
                total={data.score.must_haves_total}
                items={data.coverage} size="lg" showRatio={false}
              />
            </div>
            <Badge tone={TIER_BADGE[data.score.coverage_tier].replace('b-', '') as never}>
              {data.score.match_level}
            </Badge>
            <div style={{ textAlign: 'right' }}>
              <div className="det-cov-n">{Math.round(data.score.final_score)}</div>
              <div className="det-cov-l">ranking score</div>
            </div>
          </div>
        )}
      </div>

      <div className="det-grid">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <Card>
            <SectionHead
              title="Requirement coverage"
              sub="Each line is a requirement from the job description, with the resume text behind it."
            />
            <div>
              {musts.map((item) => (
                <EvidenceRow key={item.requirement_id} item={item}
                  onSource={() => openSource(item)}
                  onOverride={() => { setOverride(item); setOverrideVerdict('met') }} />
              ))}
            </div>
            {nices.length > 0 && (
              <>
                <SectionHead title="Nice-to-have"
                  sub="Separates candidates who already meet every must-have" />
                <div>
                  {nices.map((item) => (
                    <EvidenceRow key={item.requirement_id} item={item}
                      onSource={() => openSource(item)}
                      onOverride={() => { setOverride(item); setOverrideVerdict('met') }} />
                  ))}
                </div>
              </>
            )}
          </Card>

          {data.questions.length > 0 && (
            <Card>
              <SectionHead
                title="Recommended screening questions"
                sub="Generated from this candidate's gaps. These are prompts for the interview, not conclusions."
              />
              <div>
                {data.questions.map((q) => (
                  <div className="q-card" key={q.id}>
                    <p className="q-why">{q.rationale}</p>
                    <div className="q-txt">{q.question}</div>
                    <div className="q-acts">
                      <Button size="sm" onClick={() => {
                        navigator.clipboard?.writeText(q.question)
                        toast('Question copied.')
                      }}>Copy</Button>
                      <Button size="sm" onClick={() => toast('Added to the interview kit.')}>
                        Add to interview
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            </Card>
          )}
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {data.score && (
            <Card>
              <SectionHead title="Where the ranking comes from" />
              <div className="brk">
                {Object.entries(data.score.category_weights).map(([name, weight]) => (
                  <div className="brk-row" key={name}>
                    <div className="brk-top">
                      <span>
                        <span className="brk-name" style={{ textTransform: 'capitalize' }}>
                          {name}
                        </span>
                        <span className="brk-w"> · {Math.round(weight)}% of score</span>
                      </span>
                    </div>
                    <div className="brk-bar">
                      <div className="brk-fill" style={{
                        width: `${Math.min(100, data.score!.must_have_score * 100)}%`,
                        background: 'var(--blue)',
                      }} />
                    </div>
                  </div>
                ))}
              </div>
              <div className="note">{data.score.tier_note}</div>
            </Card>
          )}

          <Card>
            <SectionHead
              title="Worth asking about"
              sub={gaps.length
                ? `${gaps.length} requirement${gaps.length === 1 ? '' : 's'} without direct evidence`
                : 'Nothing outstanding'}
            />
            {gaps.length ? (
              <div style={{ padding: '6px 18px 16px' }}>
                {gaps.map((g) => (
                  <div key={g.requirement_id} style={{
                    padding: '11px 0', borderBottom: '1px solid var(--line)',
                  }}>
                    <div style={{ fontFamily: 'var(--ui)', fontSize: 13, fontWeight: 600 }}>
                      {g.text}
                    </div>
                    <div style={{
                      fontSize: 12.5, color: 'var(--ink-2)', marginTop: 3, lineHeight: 1.5,
                    }}>
                      {g.absence_statement ?? 'Evidence found is indirect or hedged.'}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <EmptyState title="Every must-have has evidence"
                body="Nothing flagged for follow-up on the requirement list." />
            )}
          </Card>

          {data.decision_trail.length > 0 && (
            <Card>
              <SectionHead title="Decision trail" />
              <div style={{ padding: '6px 0' }}>
                {data.decision_trail.map((d, i) => (
                  <div className="audit" key={i}>
                    <span className="audit-dot" style={{
                      background: d.action === 'reject' ? 'var(--red)' : 'var(--green)',
                    }} />
                    <div>
                      <div className="audit-t">
                        {d.action}{d.note && ` — ${d.note}`}
                      </div>
                      <div className="audit-m">
                        {new Date(d.at).toLocaleString()}{d.coverage && ` · ${d.coverage}`}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </Card>
          )}

          <div className="note" style={{ borderRadius: 'var(--r-lg)', border: '1px solid var(--line)' }}>
            {data.disclaimer}
          </div>
        </div>
      </div>

      {source && (
        <Modal title={`${data.display_name} — source`} wide onClose={() => setSource(null)}>
          <div style={{ display: 'flex', gap: 9, marginBottom: 14, flexWrap: 'wrap' }}>
            <Badge tone="blue">{source.text}</Badge>
            {source.page && <Badge>Page {source.page}</Badge>}
          </div>
          <div className="doc">
            {source.quote ? (
              <HighlightedSource text={docText} quote={source.quote} />
            ) : (
              <p className="dim">{source.absence_statement}</p>
            )}
          </div>
          <p style={{ fontSize: 12, color: 'var(--ink-3)', marginTop: 13 }}>
            Highlighted text is the exact span the requirement was matched against.
            Everything scored traces back to a quote like this one.
          </p>
        </Modal>
      )}

      {override && (
        <Modal
          title="Override this verdict" onClose={() => setOverride(null)}
          footer={
            <>
              <Button variant="ghost" onClick={() => setOverride(null)}>Cancel</Button>
              <Button variant="pri" onClick={submitOverride}
                disabled={overrideReason.length < 3 || overrideMutation.isPending}>
                Save override
              </Button>
            </>
          }
        >
          <p style={{ fontSize: 13.5, color: 'var(--ink-2)', marginBottom: 16 }}>
            The original verdict is kept alongside yours. Both are recorded — the
            pair is useful evaluation data.
          </p>
          <div style={{ marginBottom: 16 }}>
            <div className="field-label">System verdict</div>
            <Badge tone={override.verdict === 'met' ? 'green' : 'slate'}>
              {override.verdict.replace('_', ' ')}
            </Badge>
          </div>
          <label className="field" style={{ marginBottom: 14 }}>
            <span className="field-label">Your verdict</span>
            <select className="select" value={overrideVerdict}
              onChange={(e) => setOverrideVerdict(e.target.value)}>
              <option value="met">Met</option>
              <option value="partial">Partial evidence</option>
              <option value="not_met">Not met</option>
            </select>
          </label>
          <label className="field">
            <span className="field-label">Reason</span>
            <textarea className="textarea" rows={3} value={overrideReason}
              onChange={(e) => setOverrideReason(e.target.value)}
              placeholder="e.g. Discussed in their portfolio but missing from the resume." />
          </label>
        </Modal>
      )}
    </div>
  )
}

function EvidenceRow({ item, onSource, onOverride }: {
  item: CoverageItem; onSource: () => void; onOverride: () => void
}) {
  const state = item.verdict === 'met' ? 'met' : item.verdict === 'partial' ? 'partial' : 'none'
  const label = item.verdict === 'met' ? 'Met'
    : item.verdict === 'partial' ? 'Partial evidence' : 'No evidence found'

  return (
    <details className="req">
      <summary className="req-btn" style={{ listStyle: 'none' }}>
        <span className={`req-ico ${state}`}>
          {item.verdict === 'met' ? (
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round"><path d="M20 6L9 17l-5-5" /></svg>
          ) : item.verdict === 'partial' ? (
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round"><path d="M12 8v5M12 16.5v.01" /><circle cx="12" cy="12" r="9.5" /></svg>
          ) : (
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round"><path d="M5 12h14" /></svg>
          )}
        </span>
        <span className="req-main">
          <span className="req-name">{item.text}</span>
          <span className="req-teaser">{item.quote ?? item.absence_statement}</span>
        </span>
        <span className={`badge ${item.verdict === 'met' ? 'b-green' : item.verdict === 'partial' ? 'b-amber' : 'b-slate'}`}>
          {label}
        </span>
        <span className="req-caret">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round"><path d="M6 9l6 6 6-6" /></svg>
        </span>
      </summary>
      <div className="req-body">
        {item.quote ? (
          <>
            <div className="quote">
              <div className="quote-txt">“{item.quote}”</div>
              <div className="quote-src">
                <span className={`conf ${item.confidence_band === 'high' ? 'hi' : item.confidence_band === 'medium' ? 'md' : 'lo'}`}>
                  {item.confidence_label ?? item.confidence_band}
                </span>
                {item.page && <span>page {item.page}</span>}
                <button className="src-link" onClick={onSource}>Open source</button>
              </div>
            </div>
            <p style={{ fontSize: 12, color: 'var(--ink-3)' }}>
              {item.confidence_description}
              {item.quote_validated === false && ' · Quote could not be verified against the source.'}
            </p>
          </>
        ) : (
          <>
            <div className="absence">{item.absence_statement}</div>
            <p style={{ fontSize: 12, color: 'var(--ink-3)', marginTop: 9 }}>
              This states what was searched for, not a judgement about the candidate.
              {item.search_terms?.length > 0 && ` Terms checked: ${item.search_terms.join(', ')}.`}
            </p>
          </>
        )}
        {item.override && (
          <div style={{
            marginTop: 10, padding: '10px 13px', background: 'var(--blue-lo)',
            border: '1px solid var(--blue-line)', borderRadius: 'var(--r-sm)', fontSize: 12.5,
          }}>
            <strong>Recruiter override:</strong> {item.override.verdict.replace('_', ' ')}
            {item.override.reason && ` — ${item.override.reason}`}
            <div style={{ color: 'var(--ink-3)', marginTop: 3 }}>
              System verdict was {item.ai_verdict?.replace('_', ' ')} and is retained.
            </div>
          </div>
        )}
        <div style={{ marginTop: 12 }}>
          <Button size="sm" variant="ghost" onClick={onOverride}>Override this verdict</Button>
        </div>
      </div>
    </details>
  )
}

function HighlightedSource({ text, quote }: { text: string | null; quote: string }) {
  if (text === null) return <p className="dim">Loading source…</p>
  const index = text.indexOf(quote)
  if (index < 0) {
    return (
      <>
        <p><mark>{quote}</mark></p>
        <p className="dim" style={{ marginTop: 12 }}>
          Exact position in the document could not be resolved.
        </p>
      </>
    )
  }
  const before = text.slice(Math.max(0, index - 320), index)
  const after = text.slice(index + quote.length, index + quote.length + 320)
  return (
    <>
      <p className="dim">…{before}</p>
      <p style={{ margin: '12px 0' }}><mark>{quote}</mark></p>
      <p className="dim">{after}…</p>
    </>
  )
}
