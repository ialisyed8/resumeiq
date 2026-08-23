/**
 * Screening results — the most important screen in the product.
 *
 * Rows are grouped under coverage-tier headers and sorting operates inside a
 * tier, never across it. The gate is structural in the UI as well as in the
 * scorer: a recruiter cannot sort a candidate past the mandatory requirements
 * they are missing.
 */

import { useMemo, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { CoverageMeter } from '@/components/CoverageMeter'
import { Badge, Button, Card, Drawer, EmptyState, Modal, SkeletonRows, TIER_BADGE, TIER_COLOR } from '@/components/ui'
import { useDecision, useQuarantine, useResults, useScreening } from '@/hooks/queries'
import { useToast } from '@/hooks/useToast'
import type { CandidateRow, CoverageTier } from '@/api/types'

const TIER_ORDER: CoverageTier[] = ['meets_all', 'one_short', 'multiple_gaps']
const TIER_LABEL: Record<CoverageTier, string> = {
  meets_all: 'Meets every must-have',
  one_short: 'One requirement short',
  multiple_gaps: 'Multiple gaps',
}

export default function Results() {
  const { batchId = '' } = useParams()
  const navigate = useNavigate()
  const { toast } = useToast()
  const [params, setParams] = useSearchParams()

  const [blind, setBlind] = useState(params.get('blind') !== 'false')
  const [selected, setSelected] = useState<string[]>([])
  const [filtersOpen, setFiltersOpen] = useState(false)
  const [decision, setDecision] = useState<{ row: CandidateRow; action: string } | null>(null)
  const [note, setNote] = useState('')

  const query = params.get('q') ?? ''
  const tier = params.get('tier') ?? 'all'
  const sort = params.get('sort') ?? 'rank'
  const page = Number(params.get('page') ?? 1)

  const { data: screening } = useScreening(batchId)
  const { data, isLoading, isFetching } = useResults(batchId, {
    page, page_size: 25, tier, q: query || undefined, sort, blind,
  })
  const { data: quarantine } = useQuarantine(batchId)
  const decide = useDecision(batchId)

  const update = (patch: Record<string, string | undefined>) => {
    const next = new URLSearchParams(params)
    Object.entries(patch).forEach(([k, v]) => {
      if (v === undefined || v === 'all' || v === '') next.delete(k)
      else next.set(k, v)
    })
    if (!('page' in patch)) next.delete('page')
    setParams(next, { replace: true })
  }

  const grouped = useMemo(() => {
    const groups: Record<CoverageTier, CandidateRow[]> = {
      meets_all: [], one_short: [], multiple_gaps: [],
    }
    data?.items.forEach((row) => groups[row.coverage_tier].push(row))
    return groups
  }, [data])

  const toggleBlind = () => {
    const next = !blind
    setBlind(next)
    update({ blind: next ? undefined : 'false' })
    toast(
      next ? 'Blind screening on — names and photos hidden.'
           : 'Blind screening off — identifying details visible. This is logged.',
      next ? 'ok' : 'warn',
    )
  }

  const toggleSelect = (id: string) => {
    setSelected((current) => {
      if (current.includes(id)) return current.filter((x) => x !== id)
      if (current.length >= 4) {
        toast('Compare up to four candidates at a time.', 'warn')
        return current
      }
      return [...current, id]
    })
  }

  const submitDecision = async () => {
    if (!decision) return
    const result = await decide.mutateAsync({
      candidateId: decision.row.candidate_id,
      action: decision.action,
      note: note || undefined,
    })
    toast(result.message)
    setDecision(null)
    setNote('')
  }

  return (
    <div className="wrap page">
      <div className="job-bar">
        <div style={{ minWidth: 0 }}>
          <h1 style={{ fontSize: 22 }}>{screening?.job.title ?? 'Screening'}</h1>
          <div className="job-meta">
            <span className="num">{screening?.total_documents ?? 0}</span>
            <span>resumes submitted</span>
            <span className="sep" />
            <span className="num">{data?.total ?? 0}</span>
            <span>ranked</span>
            {(quarantine?.count ?? 0) > 0 && (
              <>
                <span className="sep" />
                <span style={{ color: 'var(--amber)' }}>
                  {quarantine?.count} held for review
                </span>
              </>
            )}
          </div>
        </div>
        <button
          className={`blind ${blind ? '' : 'off'}`}
          onClick={toggleBlind} role="switch" aria-checked={blind}
        >
          <span className="blind-txt">
            <span className="blind-t">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 2l8 4v6c0 5-3.4 8.9-8 10-4.6-1.1-8-5-8-10V6z" />
              </svg>
              Blind screening
            </span>
            <span className="blind-s">
              {blind ? 'Names and photos hidden' : 'Identifying details visible'}
            </span>
          </span>
          <span className="toggle" aria-hidden="true" aria-checked={blind} />
        </button>
      </div>

      <div className="tiers">
        {TIER_ORDER.map((key) => (
          <button
            key={key}
            className={`tier-card ${tier === key ? 'on' : ''}`}
            style={{ borderLeftColor: TIER_COLOR[key] }}
            onClick={() => update({ tier: tier === key ? 'all' : key })}
            aria-pressed={tier === key}
          >
            <div className="tc-val">{data?.tier_counts?.[key] ?? 0}</div>
            <div className="tc-lab">{TIER_LABEL[key]}</div>
          </button>
        ))}
      </div>

      {(quarantine?.count ?? 0) > 0 && (
        <QuarantinePanel items={quarantine!.items} note={quarantine!.note} />
      )}

      <Card>
        <div className="toolbar">
          <label className="search">
            <span className="sr">Search candidates</span>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
              <circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" />
            </svg>
            <input
              className="input" placeholder="Search candidates or skills…"
              defaultValue={query}
              onChange={(e) => {
                const value = e.target.value
                window.clearTimeout((window as never as { _t: number })._t)
                ;(window as never as { _t: number })._t = window.setTimeout(
                  () => update({ q: value || undefined }), 300,
                )
              }}
            />
          </label>
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 7 }}>
            <Button size="sm" onClick={() => setFiltersOpen(true)}>Filters</Button>
            <select
              className="select" style={{ width: 176, height: 31, fontSize: 12.5 }}
              value={sort} onChange={(e) => update({ sort: e.target.value })}
            >
              <option value="rank">Coverage, then score</option>
              <option value="score">Score within tier</option>
              <option value="experience">Experience</option>
            </select>
          </div>
        </div>

        {selected.length > 0 && (
          <div className="selbar">
            <span className="selbar-t">{selected.length} selected</span>
            <div style={{ display: 'flex', gap: 7 }}>
              <Button size="sm" onClick={() => setSelected([])}>Clear</Button>
              <Button
                variant="pri" size="sm"
                disabled={selected.length < 2}
                onClick={() => navigate(`/screenings/${batchId}/compare?ids=${selected.join(',')}`)}
              >
                Compare {selected.length}
              </Button>
            </div>
          </div>
        )}

        <div className={isFetching && !isLoading ? 'refetching' : ''}>
          {isLoading ? (
            <SkeletonRows count={8} />
          ) : !data?.items.length ? (
            <EmptyState
              title="No candidates match these filters"
              body="Try widening the coverage tier or clearing your search."
              action={<Button onClick={() => setParams(new URLSearchParams())}>Clear all filters</Button>}
            />
          ) : (
            <>
              <div className="tbl-scroll">
                <table className="tbl">
                  <thead>
                    <tr>
                      <th className="chk-cell"><span className="sr">Select</span></th>
                      <th>Rank</th><th>Candidate</th><th>Experience</th>
                      <th>Requirement coverage</th><th>Score</th>
                      <th style={{ textAlign: 'right' }}>Decision</th>
                    </tr>
                  </thead>
                  <tbody>
                    {TIER_ORDER.flatMap((key) => {
                      const rows = grouped[key]
                      if (!rows.length) return []
                      return [
                        <tr className="tier-row" key={`h-${key}`}>
                          <td colSpan={7}>
                            <div className="tier-in">
                              <span className="tier-dot" style={{ background: TIER_COLOR[key] }} />
                              <span className="tier-lab" style={{ color: TIER_COLOR[key] }}>
                                {TIER_LABEL[key]}
                              </span>
                              <span className="tier-n">
                                {rows.length} candidate{rows.length === 1 ? '' : 's'}
                              </span>
                            </div>
                          </td>
                        </tr>,
                        ...rows.map((row) => (
                          <Row
                            key={row.candidate_id} row={row}
                            selected={selected.includes(row.candidate_id)}
                            onToggle={() => toggleSelect(row.candidate_id)}
                            onView={() => navigate(`/screenings/${batchId}/candidates/${row.candidate_id}`)}
                            onDecide={(action) => { setDecision({ row, action }); setNote('') }}
                          />
                        )),
                      ]
                    })}
                  </tbody>
                </table>
              </div>

              <div className="cand-cards">
                {data.items.map((row) => (
                  <MobileCard
                    key={row.candidate_id} row={row}
                    selected={selected.includes(row.candidate_id)}
                    onToggle={() => toggleSelect(row.candidate_id)}
                    onView={() => navigate(`/screenings/${batchId}/candidates/${row.candidate_id}`)}
                    onDecide={(action) => { setDecision({ row, action }); setNote('') }}
                  />
                ))}
              </div>
            </>
          )}
        </div>

        {data && data.total > data.page_size && (
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '13px 16px', borderTop: '1px solid var(--line)', flexWrap: 'wrap', gap: 10,
          }}>
            <span style={{ fontSize: 12.5, color: 'var(--ink-3)' }}>
              Showing {(page - 1) * data.page_size + 1}–
              {Math.min(page * data.page_size, data.total)} of {data.total}
            </span>
            <div style={{ display: 'flex', gap: 5 }}>
              <Button size="sm" disabled={page <= 1}
                onClick={() => update({ page: String(page - 1) })}>Previous</Button>
              <Button size="sm" disabled={page * data.page_size >= data.total}
                onClick={() => update({ page: String(page + 1) })}>Next</Button>
            </div>
          </div>
        )}
      </Card>

      <p style={{
        fontSize: 12, color: 'var(--ink-3)', marginTop: 14, textAlign: 'center',
        maxWidth: 640, marginLeft: 'auto', marginRight: 'auto', lineHeight: 1.6,
      }}>
        {data?.disclaimer}
      </p>

      {filtersOpen && (
        <Drawer
          title="Filters" onClose={() => setFiltersOpen(false)}
          footer={
            <>
              <Button style={{ flex: 1 }} onClick={() => { setParams(new URLSearchParams()); setFiltersOpen(false) }}>
                Clear all
              </Button>
              <Button variant="pri" style={{ flex: 1 }} onClick={() => setFiltersOpen(false)}>
                Show {data?.total ?? 0} results
              </Button>
            </>
          }
        >
          <FilterGroup
            title="Requirement coverage" value={tier}
            options={[['all', 'All candidates'], ...TIER_ORDER.map((t) => [t, TIER_LABEL[t]] as [string, string])]}
            onChange={(v) => update({ tier: v })}
          />
          <FilterGroup
            title="Decision status" value={params.get('decision_status') ?? 'all'}
            options={[['all', 'All'], ['new', 'New'], ['shortlisted', 'Shortlisted'],
                      ['interview', 'Interview'], ['rejected', 'Rejected'], ['on_hold', 'On hold']]}
            onChange={(v) => update({ decision_status: v })}
          />
        </Drawer>
      )}

      {decision && (
        <Modal
          title={`${decision.action === 'shortlist' ? 'Shortlist' :
                   decision.action === 'reject' ? 'Reject' : 'Move to interview'} ${decision.row.display_name}`}
          onClose={() => setDecision(null)}
          footer={
            <>
              <Button variant="ghost" onClick={() => setDecision(null)}>Cancel</Button>
              <Button
                variant={decision.action === 'reject' ? 'danger' : 'pri'}
                onClick={submitDecision} disabled={decide.isPending}
              >
                {decide.isPending ? 'Saving…' : 'Confirm'}
              </Button>
            </>
          }
        >
          <p style={{ fontSize: 13.5, color: 'var(--ink-2)', marginBottom: 16 }}>
            This decision is recorded in the audit trail with your name and the
            requirement coverage at the time.
          </p>
          <Card pad className="" >
            <div style={{ display: 'flex', alignItems: 'center', gap: 13, flexWrap: 'wrap' }}>
              <span className="cand-id">{decision.row.reference}</span>
              <div>
                <div className="cand-name">{decision.row.display_name}</div>
                <div className="cand-meta">{decision.row.title}</div>
              </div>
              <div style={{ marginLeft: 'auto' }}>
                <CoverageMeter
                  met={decision.row.must_haves_met}
                  total={decision.row.must_haves_total}
                />
              </div>
            </div>
          </Card>
          <label className="field" style={{ marginTop: 16 }}>
            <span className="field-label">Note (optional)</span>
            <textarea
              className="textarea" rows={3} value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="e.g. Strong React and accessibility evidence; ask about team size."
            />
          </label>
        </Modal>
      )}
    </div>
  )
}

function Row({ row, selected, onToggle, onView, onDecide }: {
  row: CandidateRow; selected: boolean
  onToggle: () => void; onView: () => void; onDecide: (a: string) => void
}) {
  return (
    <tr className={selected ? 'sel' : ''}>
      <td className="chk-cell">
        <input type="checkbox" checked={selected} onChange={onToggle}
          aria-label={`Select ${row.display_name}`} />
      </td>
      <td className="num" style={{ color: 'var(--ink-3)', fontSize: 13 }}>{row.rank}</td>
      <td>
        <div className="cand-cell">
          <span className="cand-id">{row.reference}</span>
          <span style={{ minWidth: 0 }}>
            <span className="cand-name">{row.display_name}</span>
            <div className="cand-meta">
              {row.title}{row.experience_years != null && ` · ${row.experience_years} yrs`}
            </div>
          </span>
        </div>
      </td>
      <td className="num">{row.experience_years != null ? `${row.experience_years} yrs` : '—'}</td>
      <td style={{ minWidth: 210 }}>
        <CoverageMeter met={row.must_haves_met} total={row.must_haves_total} />
        {row.missing_requirements.length === 0 ? (
          <div className="gap-note clean">Meets every must-have</div>
        ) : (
          <div className="gap-note">
            Missing: {row.missing_requirements.slice(0, 3).join(', ')}
            {row.missing_requirements.length > 3 && ` +${row.missing_requirements.length - 3} more`}
          </div>
        )}
      </td>
      <td>
        <div className="score-cell">
          <span className="score-n">{Math.round(row.final_score)}</span>
          <Badge tone={TIER_BADGE[row.coverage_tier].replace('b-', '') as never}>
            {row.match_level}
          </Badge>
        </div>
      </td>
      <td>
        <div className="row-acts">
          {row.decision_status === 'new' ? (
            <>
              <Button variant="pri" size="sm" onClick={() => onDecide('shortlist')}>Shortlist</Button>
              <Button size="sm" onClick={() => onDecide('reject')}>Reject</Button>
            </>
          ) : (
            <Badge tone={row.decision_status === 'shortlisted' ? 'green' : 'slate'}>
              {row.decision_status}
            </Badge>
          )}
          <Button size="sm" onClick={onView}>View</Button>
        </div>
      </td>
    </tr>
  )
}

function MobileCard({ row, selected, onToggle, onView, onDecide }: {
  row: CandidateRow; selected: boolean
  onToggle: () => void; onView: () => void; onDecide: (a: string) => void
}) {
  return (
    <div className={`cand-card ${selected ? 'sel' : ''}`}>
      <div className="cc-top">
        <div className="cand-cell">
          <input type="checkbox" checked={selected} onChange={onToggle}
            style={{ width: 15, height: 15, accentColor: 'var(--blue)' }} aria-label="Select" />
          <span className="cand-id">{row.reference}</span>
          <span>
            <span className="cand-name">{row.display_name}</span>
            <div className="cand-meta">
              {row.title}{row.experience_years != null && ` · ${row.experience_years} yrs`}
            </div>
          </span>
        </div>
        <span className="score-n">{Math.round(row.final_score)}</span>
      </div>
      <CoverageMeter met={row.must_haves_met} total={row.must_haves_total} />
      <div style={{ marginTop: 9, display: 'flex', gap: 7, flexWrap: 'wrap' }}>
        <Badge tone={TIER_BADGE[row.coverage_tier].replace('b-', '') as never}>
          {row.match_level}
        </Badge>
      </div>
      {row.missing_requirements.length > 0 ? (
        <div className="gap-note" style={{ marginTop: 8 }}>
          Missing: {row.missing_requirements.slice(0, 2).join(', ')}
        </div>
      ) : (
        <div className="gap-note clean" style={{ marginTop: 8 }}>Meets every must-have</div>
      )}
      <div className="cc-acts">
        <Button variant="pri" size="sm" onClick={() => onDecide('shortlist')}>Shortlist</Button>
        <Button size="sm" onClick={() => onDecide('reject')}>Reject</Button>
        <Button size="sm" onClick={onView}>View</Button>
      </div>
    </div>
  )
}

function QuarantinePanel({ items, note }: { items: QuarantineItemT[]; note: string }) {
  return (
    <Card className="" >
      <div className="sec-head" style={{
        borderBottomColor: 'var(--amber-line)', background: 'var(--amber-lo)',
        borderRadius: 'var(--r-lg) var(--r-lg) 0 0',
      }}>
        <div>
          <h3 style={{ color: '#7A4700' }}>Needs manual review</h3>
          <div className="sub" style={{ color: '#8A5200' }}>{note}</div>
        </div>
        <Badge tone="amber">{items.length} resumes</Badge>
      </div>
      {items.map((item) => (
        <div className="quar" key={item.document_id}>
          <div className="quar-main">
            <div className="quar-name">{item.filename}</div>
            <div className="quar-why">{item.reason}</div>
          </div>
        </div>
      ))}
    </Card>
  )
}
type QuarantineItemT = { document_id: string; filename: string; reason: string }

function FilterGroup({ title, value, options, onChange }: {
  title: string; value: string
  options: [string, string][]; onChange: (v: string) => void
}) {
  return (
    <div className="fgroup">
      <h5>{title}</h5>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
        {options.map(([v, label]) => (
          <label className="chk" key={v}>
            <input type="radio" checked={value === v} onChange={() => onChange(v)}
              style={{ accentColor: 'var(--blue)' }} />
            <span>{label}</span>
          </label>
        ))}
      </div>
    </div>
  )
}
