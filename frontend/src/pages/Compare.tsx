/**
 * Side-by-side comparison matrix.
 *
 * Rows are requirements, columns are candidates. Reading across a row shows
 * which candidates have evidence for that requirement — which is the question
 * a recruiter actually has at this point, and the one a list of scores cannot
 * answer.
 */

import { useSearchParams, useParams, useNavigate } from 'react-router-dom'
import { useQueries } from '@tanstack/react-query'
import { api } from '@/api/client'
import { CoverageMeter } from '@/components/CoverageMeter'
import { Badge, Button, Card, EmptyState, SkeletonRows, TIER_BADGE } from '@/components/ui'
import type { CandidateDetail } from '@/api/types'

export default function Compare() {
  const { batchId = '' } = useParams()
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const ids = (params.get('ids') ?? '').split(',').filter(Boolean)

  const results = useQueries({
    queries: ids.map((id) => ({
      queryKey: ['candidate', batchId, id, true],
      queryFn: () => api.get<CandidateDetail>(
        `/screenings/${batchId}/candidates/${id}?blind=true`,
      ),
    })),
  })

  if (!ids.length) {
    return (
      <div className="wrap page">
        <EmptyState
          title="No candidates selected"
          body="Pick two to four candidates from the ranked list to compare them."
          action={<Button onClick={() => navigate(`/screenings/${batchId}`)}>Back to results</Button>}
        />
      </div>
    )
  }

  if (results.some((r) => r.isLoading)) {
    return <div className="wrap page"><Card><SkeletonRows count={6} /></Card></div>
  }

  const candidates = results.map((r) => r.data).filter(Boolean) as CandidateDetail[]
  if (!candidates.length) {
    return <div className="wrap page"><EmptyState title="Could not load those candidates" /></div>
  }

  // Requirements are identical across candidates in a batch; take the first.
  const requirements = candidates[0].coverage

  const verdictFor = (candidate: CandidateDetail, requirementId: string) =>
    candidate.coverage.find((c) => c.requirement_id === requirementId)?.verdict ?? 'not_met'

  return (
    <div className="wrap page">
      <Button variant="ghost" size="sm" style={{ marginBottom: 14 }}
        onClick={() => navigate(`/screenings/${batchId}`)}>
        ← Back to ranked list
      </Button>

      <div className="ph">
        <h1>Compare candidates</h1>
        <p className="ph-sub">
          Each row is a requirement. Blind screening stays on here — compare the
          evidence, not the people.
        </p>
      </div>

      <Card>
        <div className="tbl-scroll">
          <table className="tbl cmp">
            <thead>
              <tr>
                <th style={{ minWidth: 220 }}>Requirement</th>
                {candidates.map((c) => (
                  <th key={c.candidate_id} style={{ minWidth: 170 }}>
                    <div className="cand-name">{c.display_name}</div>
                    <div className="cand-meta">{c.title}</div>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              <tr>
                <td style={{ fontWeight: 600 }}>Coverage</td>
                {candidates.map((c) => (
                  <td key={c.candidate_id}>
                    {c.score && (
                      <>
                        <CoverageMeter
                          met={c.score.must_haves_met}
                          total={c.score.must_haves_total}
                          items={c.coverage}
                        />
                        <div style={{ marginTop: 6 }}>
                          <Badge tone={TIER_BADGE[c.score.coverage_tier].replace('b-', '') as never}>
                            {c.score.match_level}
                          </Badge>
                        </div>
                      </>
                    )}
                  </td>
                ))}
              </tr>
              <tr>
                <td style={{ fontWeight: 600 }}>Experience</td>
                {candidates.map((c) => (
                  <td key={c.candidate_id} className="num">
                    {c.experience_years != null ? `${c.experience_years} yrs` : '—'}
                  </td>
                ))}
              </tr>

              {requirements.map((req) => (
                <tr key={req.requirement_id}>
                  <td>
                    <div style={{ fontFamily: 'var(--ui)', fontSize: 13 }}>{req.text}</div>
                    <div style={{ fontSize: 11.5, color: 'var(--ink-3)' }}>
                      {req.necessity === 'must_have' ? 'Must-have' : 'Nice-to-have'}
                    </div>
                  </td>
                  {candidates.map((c) => {
                    const verdict = verdictFor(c, req.requirement_id)
                    return (
                      <td key={c.candidate_id}>
                        <span className={`cmp-cell ${verdict}`}>
                          {verdict === 'met' ? 'Evidence found'
                            : verdict === 'partial' ? 'Partial'
                            : 'No evidence'}
                        </span>
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <div style={{ display: 'flex', gap: 9, marginTop: 16, flexWrap: 'wrap' }}>
        {candidates.map((c) => (
          <Button key={c.candidate_id} size="sm"
            onClick={() => navigate(`/screenings/${batchId}/candidates/${c.candidate_id}`)}>
            Open {c.display_name}
          </Button>
        ))}
      </div>
    </div>
  )
}
