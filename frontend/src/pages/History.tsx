/**
 * Screening history. Past batches are reopenable, not archived summaries —
 * evidence and scorer version are retained, so any historical ranking can be
 * inspected or re-scored.
 */

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'
import { Badge, Button, Card, EmptyState, SectionHead, SkeletonRows } from '@/components/ui'

interface ScreeningListItem {
  id: string
  name: string
  job_title: string
  status: string
  total_documents: number
  quarantined_count: number
  created_at: string
  completed_at: string | null
}

export default function History() {
  const navigate = useNavigate()
  const [page, setPage] = useState(1)

  const { data, isLoading } = useQuery({
    queryKey: ['screenings', page],
    queryFn: () => api.get<{ items: ScreeningListItem[]; total: number; page_size: number }>(
      `/screenings?page=${page}&page_size=20`,
    ),
  })

  return (
    <div className="wrap page">
      <div className="ph">
        <h1>Screening history</h1>
        <p className="ph-sub">
          Every batch keeps its evidence and scorer version, so past results can
          be reopened, re-scored, and audited.
        </p>
      </div>

      <Card>
        <SectionHead title="All screenings" sub={`${data?.total ?? 0} total`} />
        {isLoading ? (
          <SkeletonRows count={6} />
        ) : !data?.items.length ? (
          <EmptyState
            title="No screenings yet"
            body="Create your first screening to start ranking candidates."
            action={<Button variant="pri" onClick={() => navigate('/screenings/new')}>New screening</Button>}
          />
        ) : (
          <div className="tbl-scroll">
            <table className="tbl">
              <thead>
                <tr>
                  <th>Screening</th><th>Resumes</th><th>Held for review</th>
                  <th>Status</th><th>Run</th><th style={{ textAlign: 'right' }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((s) => (
                  <tr key={s.id}>
                    <td>
                      <div style={{ fontFamily: 'var(--ui)', fontWeight: 600 }}>{s.job_title}</div>
                      <div style={{ fontSize: 12, color: 'var(--ink-3)' }}>{s.name}</div>
                    </td>
                    <td className="num">{s.total_documents}</td>
                    <td className="num" style={{ color: s.quarantined_count ? 'var(--amber)' : 'var(--ink-4)' }}>
                      {s.quarantined_count || '—'}
                    </td>
                    <td>
                      <Badge tone={s.status === 'completed' ? 'green'
                        : s.status === 'failed' ? 'red' : 'blue'}>
                        {s.status}
                      </Badge>
                    </td>
                    <td style={{ fontSize: 12.5, color: 'var(--ink-2)' }}>
                      {new Date(s.created_at).toLocaleDateString()}
                    </td>
                    <td>
                      <div className="row-acts">
                        <Button size="sm" onClick={() => navigate(`/screenings/${s.id}`)}>Reopen</Button>
                        <Button size="sm" onClick={() => navigate(`/reports?screening=${s.id}`)}>Report</Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {data && data.total > data.page_size && (
          <div style={{
            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
            padding: '13px 16px', borderTop: '1px solid var(--line)',
          }}>
            <span style={{ fontSize: 12.5, color: 'var(--ink-3)' }}>
              Page {page} of {Math.ceil(data.total / data.page_size)}
            </span>
            <div style={{ display: 'flex', gap: 5 }}>
              <Button size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</Button>
              <Button size="sm" disabled={page * data.page_size >= data.total}
                onClick={() => setPage(page + 1)}>Next</Button>
            </div>
          </div>
        )}
      </Card>
    </div>
  )
}
