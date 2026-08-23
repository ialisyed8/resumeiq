import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Button } from '@/components/ui'
import { useAuth } from '@/hooks/useAuth'
import { useToast } from '@/hooks/useToast'
import { ApiRequestError } from '@/api/client'

export default function Login() {
  const { login } = useAuth()
  const { toast } = useToast()
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [show, setShow] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    setBusy(true); setError(null)
    try {
      await login(email, password)
      toast('Welcome back.')
      navigate('/', { replace: true })
    } catch (e) {
      setError(e instanceof ApiRequestError ? e.message : 'Could not sign you in.')
    } finally {
      setBusy(false)
    }
  }

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
          <h1>Log in</h1>
          <p className="lede">Screen candidates against the requirements that actually matter.</p>
        </div>

        <form className="auth-row" onSubmit={(e) => { e.preventDefault(); submit() }}>
          <label className="field">
            <span className="field-label">Work email</span>
            <input className="input" type="email" autoComplete="username"
              value={email} onChange={(e) => setEmail(e.target.value)} required />
          </label>
          <label className="field">
            <span className="field-label">Password</span>
            <span className="pw-wrap">
              <input className="input" type={show ? 'text' : 'password'}
                autoComplete="current-password" value={password}
                onChange={(e) => setPassword(e.target.value)} required />
              <button type="button" className="pw-eye" onClick={() => setShow(!show)}
                aria-label={show ? 'Hide password' : 'Show password'}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z" /><circle cx="12" cy="12" r="3" />
                </svg>
              </button>
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

          <div className="auth-meta">
            <label className="chk">
              <input type="checkbox" defaultChecked /><span>Remember me</span>
            </label>
            <Link to="/forgot-password" style={{ fontSize: 13, fontWeight: 600 }}>
              Forgot password?
            </Link>
          </div>
          <Button variant="pri" size="lg" style={{ width: '100%' }} type="submit" disabled={busy}>
            {busy ? 'Signing in…' : 'Log in'}
          </Button>
        </form>

        <p className="auth-foot">
          Don't have an account? <Link to="/register">Sign up</Link>
        </p>
      </div>
    </div>
  )
}
