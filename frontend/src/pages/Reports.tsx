/**
 * Reporting. Language throughout is evidence-based: coverage counts and gaps,
 * never "weak candidate" or "AI recommends".
 */

import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'
import { Badge, Button, Card, EmptyState, SectionHead, SkeletonRows } from '@/components/ui'
import { useToast } from '@/hooks/useToast'

interface ReportData {
  screening: { id: string; name: string; job_title: string; status: string; scorer_version: string; created_at: string }
  summary: { submitted: number; screened: number; quarantined: number; failed: number }
  coverage_tiers: Record<string, number>
  requirement_coverage: { requirement: string; necessity: string; met: number; evaluated: number }[]
  top_candidates: { rank: number; reference: string; coverage: string; score: number; tier: string; decision_status: string }[]
  decisions: Record<string, number>
}

export default function Reports() {
  const [params] = useSearchParams()
  const { toast } = useToast()
  const [screeningId, setScreeningId] = useState(params.get('screening') ?? '')

  const { data: list } = useQuery({
    queryKey: ['screenings', 1],
    queryFn: () => api.get<{ items: { id: string; job_title: string; name: string }[] }>(
      '/screenings?page=1&page_size=50',
    ),
  })

  const effectiveId = screeningId || list?.items[0]?.id || ''

  const { data, isLoading } = useQuery({
    queryKey: ['report', effectiveId],
    queryFn: () => api.get<ReportData>(`/screenings/${effectiveId}/report`),
    enabled: Boolean(effectiveId),
  })

    /**
   * Two exports rather than one toggle.
   *
   * An identified export leaves the system — it gets emailed, dropped in shared
   * drives, and outlives the audit trail. Making it a separate, deliberate click
   * matches how revealing identity works elsewhere in the app, and the backend
   * records which of the two was used.
   */
  const exportCsv = (withNames: boolean) => {
    const base = import.meta.env.VITE_API_BASE ?? '/api'
    const query = withNames ? '?blind=false' : ''
    window.open(`${base}/screenings/${effectiveId}/export/csv${query}`, '_blank')
    toast(
      withNames
        ? 'Export includes candidate names. This is recorded in the audit trail.'
        : 'CSV export started.',
      withNames ? 'warn' : 'ok',
    )
  }

  if (!list?.items.length) {
    return (
      <div className="wrap page">
        <EmptyState title="Nothing to report on yet"
          body="Run a screening and its report will appear here." />
      </div>
    )
  }

  return (
    <div className="wrap page">
      <div className="ph">
        <div className="ph-top">
          <div>
            <h1>Reports</h1>
            <p className="ph-sub">Summary, requirement breakdown, and recruiter decisions.</p>
          </div>
          <div className="ph-actions">
            <select className="select" style={{ width: 260 }}
              value={effectiveId} onChange={(e) => setScreeningId(e.target.value)}>
              {list.items.map((s) => (
                <option key={s.id} value={s.id}>{s.job_title} — {s.name}</option>
              ))}
            </select>
            <Button onClick={exportCsv}>Export CSV</Button>
            <Button onClick={() => exportCsv(true)}>Export with names</Button>
          </div>
        </div>
      </div>

      {isLoading || !data ? (
        <Card><SkeletonRows count={6} /></Card>
      ) : (
        <>
          <div className="kpis stagger">
            <Kpi label="Resumes submitted" value={data.summary.submitted} />
            <Kpi label="Screened" value={data.summary.screened} />
            <Kpi label="Held for review" value={data.summary.quarantined} />
            <Kpi label="Meet every must-have" value={data.coverage_tiers.meets_all ?? 0} />
            <Kpi label="Shortlisted" value={data.decisions.shortlisted ?? 0} />
          </div>

          <div className="grid-2">
            <Card>
              <SectionHead title="Requirement breakdown"
                sub="How many candidates had evidence for each requirement" />
              <div style={{ padding: '4px 0' }}>
                {data.requirement_coverage.map((r) => (
                  <div className="gap-row" key={r.requirement}>
                    <span className="gap-name">
                      {r.requirement}
                      {r.necessity === 'nice_to_have' && (
                        <span style={{ color: 'var(--ink-3)', fontWeight: 400 }}> · nice-to-have</span>
                      )}
                    </span>
                    <span className="gap-bar">
                      <span className="gap-fill" style={{
                        width: `${r.evaluated ? Math.round((r.met / r.evaluated) * 100) : 0}%`,
                        background: 'var(--green)',
                      }} />
                    </span>
                    <span className="gap-n">{r.met} / {r.evaluated}</span>
                  </div>
                ))}
              </div>
            </Card>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              <Card>
                <SectionHead title="Candidate ranking" sub="Top 10 by coverage, then score" />
                <div className="tbl-scroll">
                  <table className="tbl">
                    <thead>
                      <tr><th>#</th><th>Candidate</th><th>Coverage</th><th>Score</th><th>Decision</th></tr>
                    </thead>
                    <tbody>
                      {data.top_candidates.map((c) => (
                        <tr key={c.reference}>
                          <td className="num">{c.rank}</td>
                          <td>{c.reference}</td>
                          <td className="num">{c.coverage}</td>
                          <td className="num">{Math.round(c.score)}</td>
                          <td>
                            {c.decision_status === 'new'
                              ? <span style={{ color: 'var(--ink-3)', fontSize: 12.5 }}>Not reviewed</span>
                              : <Badge tone={c.decision_status === 'shortlisted' ? 'green' : 'slate'}>
                                  {c.decision_status}
                                </Badge>}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>

              <Card>
                <SectionHead title="Recruiter decisions" />
                <div className="pool">
                  {(['shortlisted', 'interview', 'on_hold', 'rejected'] as const).map((k) => (
                    <div className="pool-cell" key={k}>
                      <div className="pool-val">{data.decisions[k] ?? 0}</div>
                      <div className="pool-lab" style={{ textTransform: 'capitalize' }}>
                        {k.replace('_', ' ')}
                      </div>
                    </div>
                  ))}
                </div>
                <div className="note">
                  Every decision here was made by a person and is recorded in the
                  audit trail with the coverage that was on screen at the time.
                </div>
              </Card>
            </div>
          </div>

          <p style={{ fontSize: 12, color: 'var(--ink-3)', marginTop: 14, textAlign: 'center' }}>
            Scored with {data.screening.scorer_version}. Results can be reproduced
            from stored evidence at any time.
          </p>
        </>
      )}
    </div>
  )
}

function Kpi({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="kpi">
      <div className="kpi-label">{label}</div>
      <div className="kpi-val">{value}</div>
    </div>
  )
}
