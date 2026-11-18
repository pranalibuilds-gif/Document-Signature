import { useState, type FormEvent } from 'react'
import { Link, Navigate, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/useAuth'

export function Register() {
  const { user, register } = useAuth()
  const navigate = useNavigate()
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  if (user) return <Navigate to="/dashboard" replace />

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      await register(name.trim(), email.trim(), password)
      navigate('/login?registered=1', { replace: true })
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not create your account.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="auth-page">
      <Link className="brand auth-brand" to="/login">
        <span className="brand-mark">P</span><span>papertrail</span>
      </Link>
      <section className="auth-card">
        <p className="eyebrow">GET STARTED</p>
        <h1>Create your workspace</h1>
        <p className="muted">Everything you need to prepare and send a document.</p>
        <form className="form-stack auth-form" onSubmit={submit}>
          <label className="field-label" htmlFor="register-name">Your name</label>
          <input
            id="register-name"
            className="text-input"
            autoComplete="name"
            minLength={1}
            maxLength={120}
            required
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Alex Morgan"
          />
          <label className="field-label" htmlFor="register-email">Email address</label>
          <input
            id="register-email"
            className="text-input"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="you@example.com"
          />
          <label className="field-label" htmlFor="register-password">Password</label>
          <input
            id="register-password"
            className="text-input"
            type="password"
            autoComplete="new-password"
            minLength={8}
            maxLength={72}
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder="At least 8 characters"
          />
          {error && <p className="form-error" role="alert">{error}</p>}
          <button className="button button-primary button-full" disabled={busy}>
            {busy ? 'Creating account…' : 'Create account'}
          </button>
        </form>
        <p className="auth-switch">Already have an account? <Link to="/login">Sign in</Link></p>
      </section>
      <p className="auth-footnote">Secure, simple e-signatures for the work that matters.</p>
    </main>
  )
}
