import { useNavigate } from 'react-router-dom'
import { Button, Card, SectionHead, SkeletonRows } from '@/components/ui'
import { useDashboard } from '@/hooks/queries'

export default function Dashboard() {
  const { data, isLoading } = useDashboard()
  const navigate = useNavigate()

  if (isLoading) {
    return <div className="wrap page"><Card><SkeletonRows count={6} /></Card></div>
  }

  const k = data?.kpis
  const pool = data?.pool
  const total = (pool?.meets_all ?? 0) + (pool?.one_short ?? 0) + (pool?.multiple_gaps ?? 0)

  return (
    <div className="wrap page">
      <div className="ph">
        <div className="ph-top">
          <div>
            <h1>Good morning, Alex</h1>
            <p className="ph-sub">Review your hiring activity and candidate screening progress.</p>
          </div>
          <div className="ph-actions">
            <Button variant="pri" onClick={() => navigate('/screenings/new')}>New screening</Button>
          </div>
        </div>
      </div>

      <div className="kpis stagger">
        <Kpi label="Active jobs" value={k?.active_jobs ?? 0} />
        <Kpi label="Candidates screened" value={k?.candidates_screened ?? 0} />
        <Kpi label="Shortlisted" value={k?.shortlisted ?? 0} />
        <Kpi label="Interviews" value={k?.interviews ?? 0} />
        <Kpi label="Avg. must-have coverage"
          value={k?.average_must_have_coverage ?? '—'} foot="of must-haves met" />
      </div>

      <div className="grid-2">
        <Card>
          <SectionHead title="Recent screening activity"
            sub="Batches you ran in the last 30 days" />
          <div className="tbl-scroll">
            <table className="tbl">
              <thead>
                <tr><th>Job</th><th>Resumes</th><th>Held for review</th><th>Status</th><th /></tr>
              </thead>
              <tbody>
                {data?.recent_screenings.map((s) => (
                  <tr key={s.id}>
                    <td>
                      <div style={{ fontFamily: 'var(--ui)', fontWeight: 600 }}>{s.job_title}</div>
                      <div style={{ fontSize: 12, color: 'var(--ink-3)' }}>
                        {new Date(s.created_at).toLocaleDateString()}
                      </div>
                    </td>
                    <td className="num">{s.total}</td>
                    <td className="num" style={{ color: s.quarantined ? 'var(--amber)' : 'var(--ink-4)' }}>
                      {s.quarantined || '—'}
                    </td>
                    <td>
                      <span className={`badge ${s.status === 'completed' ? 'b-green' : s.status === 'failed' ? 'b-red' : 'b-blue'}`}>
                        {s.status}
                      </span>
                    </td>
                    <td style={{ textAlign: 'right' }}>
                      <Button size="sm" onClick={() => navigate(`/screenings/${s.id}`)}>View</Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <Card>
            <SectionHead title="Candidate pool" sub="Most recent screening" />
            <div className="pool">
              <PoolCell value={pool?.meets_all ?? 0} den={` / ${total}`} label="Meet every must-have" />
              <PoolCell value={pool?.one_short ?? 0} label="One requirement short" />
              <PoolCell value={pool?.multiple_gaps ?? 0} label="Two or more gaps" />
              <PoolCell value={k?.average_must_have_coverage ?? '—'} label="Average must-haves met" />
            </div>
          </Card>

          <Card>
            <SectionHead title="Most common gaps"
              sub="Requirements missing across the pool" />
            <div>
              {data?.common_gaps.map((gap) => (
                <div className="gap-row" key={gap.requirement}>
                  <span className="gap-name">{gap.requirement}</span>
                  <span className="gap-bar">
                    <span className="gap-fill" style={{
                      width: `${total ? Math.round((gap.missing_count / total) * 100) : 0}%`,
                    }} />
                  </span>
                  <span className="gap-n">{gap.missing_count} missing</span>
                </div>
              ))}
              {!data?.common_gaps.length && (
                <div style={{ padding: 24, textAlign: 'center', color: 'var(--ink-3)', fontSize: 13 }}>
                  Run a screening to see where the pool falls short.
                </div>
              )}
            </div>
          </Card>
        </div>
      </div>
    </div>
  )
}

function Kpi({ label, value, foot }: { label: string; value: number | string; foot?: string }) {
  return (
    <div className="kpi">
      <div className="kpi-label">{label}</div>
      <div className="kpi-val">{value}</div>
      {foot && <div className="kpi-foot"><span>{foot}</span></div>}
    </div>
  )
}

function PoolCell({ value, den, label }: { value: number | string; den?: string; label: string }) {
  return (
    <div className="pool-cell">
      <div className="pool-val">{value}{den && <span className="den">{den}</span>}</div>
      <div className="pool-lab">{label}</div>
    </div>
  )
}
