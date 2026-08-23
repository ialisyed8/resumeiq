/**
 * Settings. Blind screening and bias controls are surfaced here as explained
 * policy rather than as bare toggles — a recruiter turning one off should know
 * what it does and that the change is logged.
 */

import { useState } from 'react'
import { Button, Card, SectionHead } from '@/components/ui'
import { useAuth } from '@/hooks/useAuth'
import { useToast } from '@/hooks/useToast'

const SECTIONS = ['Profile', 'Screening defaults', 'Data & privacy', 'About'] as const
type Section = typeof SECTIONS[number]

export default function Settings() {
  const { user } = useAuth()
  const { toast } = useToast()
  const [section, setSection] = useState<Section>('Profile')
  const [blindDefault, setBlindDefault] = useState(true)
  const [retention, setRetention] = useState(180)

  return (
    <div className="wrap page">
      <div className="ph">
        <h1>Settings</h1>
        <p className="ph-sub">Account, screening defaults, and data handling.</p>
      </div>

      <div className="set-grid">
        <nav className="set-nav" aria-label="Settings sections">
          {SECTIONS.map((s) => (
            <button key={s} className={`set-link ${section === s ? 'on' : ''}`}
              onClick={() => setSection(s)}>
              {s}
            </button>
          ))}
        </nav>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {section === 'Profile' && (
            <Card pad>
              <SectionHead title="Your account" />
              <label className="field" style={{ marginTop: 14 }}>
                <span className="field-label">Full name</span>
                <input className="input" defaultValue={user?.full_name} />
              </label>
              <label className="field" style={{ marginTop: 12 }}>
                <span className="field-label">Work email</span>
                <input className="input" defaultValue={user?.email} disabled />
              </label>
              <label className="field" style={{ marginTop: 12 }}>
                <span className="field-label">Role</span>
                <input className="input" defaultValue={user?.role} disabled />
              </label>
              <div style={{ marginTop: 16 }}>
                <Button variant="pri" onClick={() => toast('Profile saved.')}>Save changes</Button>
              </div>
            </Card>
          )}

          {section === 'Screening defaults' && (
            <>
              <Card pad>
                <SectionHead title="Blind screening" />
                <p style={{ fontSize: 13, color: 'var(--ink-2)', margin: '10px 0 14px' }}>
                  Hides name, photo, email, phone, and address while candidates are
                  being ranked. The scoring pipeline never reads these fields at all —
                  this controls what the interface shows you. Turning it off for a
                  screening is recorded in the audit trail.
                </p>
                <label className="chk">
                  <input type="checkbox" checked={blindDefault}
                    onChange={(e) => { setBlindDefault(e.target.checked); toast('Default updated.') }} />
                  <span>Blind screening on by default for new screenings</span>
                </label>
              </Card>

              <Card pad>
                <SectionHead title="What is never used for scoring" />
                <p style={{ fontSize: 13, color: 'var(--ink-2)', margin: '10px 0 12px' }}>
                  These are excluded by design and cannot be enabled. Each is a
                  proxy for a protected characteristic rather than a signal of
                  capability.
                </p>
                <ul style={{ fontSize: 13, color: 'var(--ink-2)', lineHeight: 1.9, paddingLeft: 18 }}>
                  <li>Name, photograph, address, nationality</li>
                  <li>Age, date of birth, graduation year</li>
                  <li>Institution name</li>
                  <li>Employment gaps — measured for context, never scored</li>
                  <li>Writing tone, sentiment, or personality</li>
                </ul>
              </Card>
            </>
          )}

          {section === 'Data & privacy' && (
            <>
              <Card pad>
                <SectionHead title="Retention" />
                <label className="field" style={{ marginTop: 12 }}>
                  <span className="field-label">Delete resumes and extracted data after</span>
                  <select className="select" value={retention}
                    onChange={(e) => { setRetention(Number(e.target.value)); toast('Retention updated.') }}>
                    <option value={90}>90 days</option>
                    <option value={180}>180 days</option>
                    <option value={365}>1 year</option>
                    <option value={730}>2 years</option>
                  </select>
                </label>
                <div className="note" style={{ marginTop: 14 }}>
                  Audit records are kept for seven years regardless of this setting,
                  because a hiring decision may need to be explained long after the
                  resume behind it is gone.
                </div>
              </Card>

              <Card pad>
                <SectionHead title="Candidate deletion" />
                <p style={{ fontSize: 13, color: 'var(--ink-2)', margin: '10px 0 0' }}>
                  A candidate can request erasure at any time. Deleting removes their
                  document from storage and their identifying details from the
                  database. The audit record of decisions taken remains, without
                  identifying information attached.
                </p>
              </Card>
            </>
          )}

          {section === 'About' && (
            <Card pad>
              <SectionHead title="About ResumeIQ" />
              <p style={{ fontSize: 13, color: 'var(--ink-2)', margin: '10px 0 14px', lineHeight: 1.7 }}>
                ResumeIQ ranks resumes against requirements you approve, and shows
                you the text behind every result. It does not decide who to hire and
                it never rejects anyone automatically — it narrows a pile so a person
                can read the right ones.
              </p>
              <div className="note">
                Scores rank resumes against a job description. They are not a
                prediction of job performance, and no evidence supports treating
                them as one.
              </div>
            </Card>
          )}
        </div>
      </div>
    </div>
  )
}
