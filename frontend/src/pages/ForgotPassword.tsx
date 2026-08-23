import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '@/api/client'
import { Button } from '@/components/ui'

export default function ForgotPassword() {
  const [email, setEmail] = useState('')
  const [sent, setSent] = useState(false)
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    setBusy(true)
    try { await api.post('/auth/forgot-password', { email }) } catch { /* response is identical either way */ }
    setSent(true)
    setBusy(false)
  }

  return (
    <div className="auth">
      <div className="auth-card">
        <div className="auth-head">
          <h1>Reset your password</h1>
          <p className="lede">
            {sent
              ? 'If that email is registered, a reset link is on its way. The link expires in 30 minutes.'
              : 'Enter your work email and we\'ll send you a reset link.'}
          </p>
        </div>

        {!sent && (
          <form className="auth-row" onSubmit={(e) => { e.preventDefault(); submit() }}>
            <label className="field">
              <span className="field-label">Work email</span>
              <input className="input" type="email" value={email}
                onChange={(e) => setEmail(e.target.value)} required />
            </label>
            <Button variant="pri" size="lg" style={{ width: '100%' }} type="submit" disabled={busy}>
              {busy ? 'Sending…' : 'Send reset link'}
            </Button>
          </form>
        )}

        <p className="auth-foot"><Link to="/login">Back to log in</Link></p>
      </div>
    </div>
  )
}
