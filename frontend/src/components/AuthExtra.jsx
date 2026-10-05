import { useEffect, useRef, useState } from 'react'

import { api } from '../api'
import { hrefFor } from '../lib/router'
import { passwordProblem } from '../lib/password'
import Icon from './Icon'
import { AuthAside } from './Login'
import PasswordField from './PasswordField'

/** "Forgot password?": asks for the email and says the same thing either way. */
export function ForgotPage({ signup }) {
  const [email, setEmail] = useState('')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState('')
  const [error, setError] = useState('')

  const submit = async (event) => {
    event.preventDefault()
    if (!email.trim()) {
      setError('Enter the email you signed up with.')
      return
    }
    setBusy(true)
    setError('')
    try {
      setDone((await api.forgotPassword(email.trim())).message)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="auth-page">
      <AuthAside title="It happens to everyone" points={['Enter your email', 'Open the link we send',
        'Choose a new password']} />
      <main className="auth-main">
        <form className="auth-card" onSubmit={submit}>
          <h1>Reset your password</h1>
          {done ? (
            <>
              <div className="mail-art" aria-hidden="true"><Icon name="mail" size={34} /></div>
              <p className="auth-sub">{done}</p>
              {signup && signup.email === false && (
                <p className="auth-notice">Email is not set up on this server, so the link goes to the administrator,
                  who can pass it on.</p>
              )}
            </>
          ) : (
            <>
              <p className="auth-sub">We will email you a link to choose a new one. It works for 30 minutes.</p>
              <label className="field">
                <span>Email</span>
                <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoFocus
                  autoComplete="email" placeholder="asha@ddma.gov.in" />
              </label>
              {error && <p className="error" role="alert">{error}</p>}
              <button className="btn btn-primary btn-block" type="submit" disabled={busy}>
                {busy && <span className="spinner" />}{busy ? 'Sending…' : 'Email me a reset link'}
              </button>
            </>
          )}
          <p className="auth-switch"><a href={hrefFor('login')}>← Back to log in</a></p>
        </form>
      </main>
    </div>
  )
}

/** The page the reset email links to: choose a new password, then you are in. */
export function ResetPage({ token, onSignedIn }) {
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async (event) => {
    event.preventDefault()
    const weak = passwordProblem(password)
    if (weak) return setError(weak)
    if (password !== confirm) return setError('The two passwords are not the same.')
    setBusy(true)
    setError('')
    try {
      const { user } = await api.resetPassword(token, password)
      onSignedIn(user, { message: 'Password changed. You are signed in.' })
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  if (!token) {
    return (
      <div className="auth-page">
        <AuthAside title="Reset your password" points={['Open the link from your email']} />
        <main className="auth-main"><div className="auth-card">
          <h1>This link is incomplete</h1>
          <p className="auth-sub">Open the whole link from the email, or ask for a new one.</p>
          <a className="btn btn-primary btn-block" href={hrefFor('forgot')}>Ask for a new link</a>
        </div></main>
      </div>
    )
  }

  return (
    <div className="auth-page">
      <AuthAside title="Choose a new password" points={['Every other device will be signed out',
        'You stay signed in here']} />
      <main className="auth-main">
        <form className="auth-card" onSubmit={submit}>
          <h1>Choose a new password</h1>
          <p className="auth-sub">Make it one you have not used here before.</p>
          <PasswordField label="New password" value={password} onChange={(e) => setPassword(e.target.value)} autoFocus />
          <label className="field">
            <span>Type it again</span>
            <input type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" />
          </label>
          {error && <p className="error" role="alert">{error}</p>}
          <button className="btn btn-primary btn-block" type="submit" disabled={busy}>
            {busy && <span className="spinner" />}{busy ? 'Saving…' : 'Save and sign in'}
          </button>
          <p className="auth-switch"><a href={hrefFor('forgot')}>Link expired? Ask for a new one</a></p>
        </form>
      </main>
    </div>
  )
}

/** The page the confirmation email links to: confirms, then signs in. */
export function VerifyPage({ token, onSignedIn }) {
  const [state, setState] = useState({ status: token ? 'working' : 'missing', message: '' })
  const asked = useRef(false)

  useEffect(() => {
    if (!token || asked.current) return
    asked.current = true
    api.verifyEmail(token)
      .then(({ user }) => onSignedIn(user, { welcome: true }))
      .catch((err) => setState({ status: 'failed', message: err.message }))
  }, [token, onSignedIn])

  return (
    <div className="auth-page">
      <AuthAside title="Confirming your email" points={['One moment']} />
      <main className="auth-main">
        <div className="auth-card">
          {state.status === 'working' && <><span className="spinner dark" /> <p className="auth-sub">Confirming your email…</p></>}
          {state.status !== 'working' && (
            <>
              <h1>That link did not work</h1>
              <p className="auth-sub">{state.message || 'The link is incomplete.'} Log in and we can send a new one.</p>
              <a className="btn btn-primary btn-block" href={hrefFor('login')}>Go to log in</a>
            </>
          )}
        </div>
      </main>
    </div>
  )
}
