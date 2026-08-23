import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, ApiRequestError } from '@/api/client'
import { Button } from '@/components/ui'
import { useAuth } from '@/hooks/useAuth'
import { useToast } from '@/hooks/useToast'

export default function Register() {
  const { login } = useAuth()
  const { toast } = useToast()
  const navigate = useNavigate()
  const [form, setForm] = useState({
    full_name: '', email: '', company: '', job_title: '', password: '',
  })
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm({ ...form, [k]: e.target.value })

  const submit = async () => {
    setBusy(true); setError(null)
    try {
      await api.post('/auth/register', form)
      await login(form.email, form.password)
      toast('Workspace created.')
      navigate('/', { replace: true })
    } catch (e) {
      setError(e instanceof ApiRequestError ? e.message : 'Could not create that account.')
    } finally {
      setBusy(false)
    }
  }

  const tooShort = form.password.length > 0 && form.password.length < 12

  return (
    <div className="auth">
      <div className="auth-card">
        <div className="auth-head">
          <div className="brand">
            <div className="brand-mark">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M3 8V5a2 2 0 0 1 2-2h3M16 3h3a2 2 0 0 1 2 2v3M21 16v3a2 2 0 0 1-2 2h-3M8 21H5a2 2 0 0 1-2-2v-3" />
                <path d="M8 12h8M8 9h5M8 15h6" />
              </svg>
            </div>
            <span className="brand-name">ResumeIQ</span>
          </div>
          <h1>Create your workspace</h1>
          <p className="lede">You'll be the administrator for your organisation.</p>
        </div>

        <form className="auth-row" onSubmit={(e) => { e.preventDefault(); submit() }}>
          <label className="field">
            <span className="field-label">Full name</span>
            <input className="input" value={form.full_name} onChange={set('full_name')} required />
          </label>
          <label className="field">
            <span className="field-label">Work email</span>
            <input className="input" type="email" autoComplete="username"
              value={form.email} onChange={set('email')} required />
          </label>
          <label className="field">
            <span className="field-label">Company</span>
            <input className="input" value={form.company} onChange={set('company')} required />
          </label>
          <label className="field">
            <span className="field-label">Job title <span style={{ color: 'var(--ink-3)' }}>(optional)</span></span>
            <input className="input" value={form.job_title} onChange={set('job_title')} />
          </label>
          <label className="field">
            <span className="field-label">Password</span>
            <input className="input" type="password" autoComplete="new-password"
              value={form.password} onChange={set('password')} required />
            <span style={{ fontSize: 12, color: tooShort ? 'var(--red)' : 'var(--ink-3)', marginTop: 5 }}>
              At least 12 characters. A passphrase works well.
            </span>
          </label>

          {error && (
            <div className="err-box" role="alert">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round">
                <path d="M12 8v5M12 16.5v.01" /><circle cx="12" cy="12" r="9.5" />
              </svg>
              <div><div className="err-t">{error}</div></div>
            </div>
          )}

          <Button variant="pri" size="lg" style={{ width: '100%' }} type="submit"
            disabled={busy || form.password.length < 12}>
            {busy ? 'Creating…' : 'Create workspace'}
          </Button>
        </form>

        <p className="auth-foot">Already have an account? <Link to="/login">Log in</Link></p>
      </div>
    </div>
  )
}
