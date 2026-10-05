import { useState } from 'react'

import { api } from '../api'
import { hrefFor } from '../lib/router'
import { loginProblem } from '../lib/session'
import Icon from './Icon'
import { Logo } from './SiteHeader'

/** The navy side of the log-in and sign-up pages. */
export function AuthAside({ title, points }) {
  return (
    <aside className="auth-aside">
      <Logo />
      <div className="auth-orbit" aria-hidden="true">
        <span className="orbit orbit-1" /><span className="orbit orbit-2" /><span className="orbit orbit-3" />
        <span className="orbit-sat"><Icon name="satellite" size={22} /></span>
        <img src="/logo-mark.png" alt="" className="orbit-earth" />
      </div>
      <h2>{title}</h2>
      <ul>
        {points.map((p) => <li key={p}><Icon name="check" size={16} /> {p}</li>)}
      </ul>
      <a className="auth-back" href={hrefFor('landing')}>← Back to the home page</a>
    </aside>
  )
}

/** Log in. The session is an HttpOnly cookie the server sets; nothing is stored here. */
export default function Login({ onSignedIn, notice }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [show, setShow] = useState(false)
  const [error, setError] = useState('')
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [resent, setResent] = useState('')

  const submit = async (event) => {
    event.preventDefault()
    const problem = loginProblem(username, password)
    if (problem) {
      setError(problem.replace('username', 'email or username'))
      return
    }
    setBusy(true)
    setError('')
    setCode('')
    setResent('')
    try {
      const { user } = await api.login(username.trim(), password)
      setPassword('')
      onSignedIn(user)
    } catch (err) {
      setError(err.message)
      setCode(err.code || '')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="auth-page">
      <AuthAside title="Welcome back"
        points={['Your past analyses are waiting in History', 'Results you ran before open instantly',
          'Reports in English and हिन्दी']} />
      <main className="auth-main">
        <form className={`auth-card ${error ? 'shake' : ''}`} onSubmit={submit} key={error}>
          <h1>Log in</h1>
          <p className="auth-sub">Use the email you signed up with, or your username.</p>
          {notice && <p className="auth-notice">{notice}</p>}
          <label className="field">
            <span>Email or username</span>
            <input autoComplete="username" value={username} autoFocus placeholder="asha@ddma.gov.in"
              onChange={(e) => setUsername(e.target.value)} />
          </label>
          <label className="field">
            <span>Password</span>
            <div className="password-field">
              <input type={show ? 'text' : 'password'} autoComplete="current-password" value={password}
                onChange={(e) => setPassword(e.target.value)} />
              <button type="button" onClick={() => setShow((s) => !s)}>{show ? 'Hide' : 'Show'}</button>
            </div>
          </label>
          <a className="forgot-link" href={hrefFor('forgot')}>Forgot password?</a>
          {error && <p className="error" role="alert">{error}</p>}
          {code === 'unverified' && (
            <button type="button" className="btn btn-outline btn-block" onClick={async () => {
              try { setResent((await api.resendVerification(username.trim())).message) } catch (err) { setResent(err.message) }
            }}>Send the confirmation link again</button>
          )}
          {resent && <p className="auth-notice ok">{resent}</p>}
          <button className="btn btn-primary btn-block" type="submit" disabled={busy}>
            {busy ? <span className="spinner" /> : null}{busy ? 'Logging in…' : 'Log in'}
          </button>
          <p className="auth-switch">New here? <a href={hrefFor('signup')}>Create an account</a></p>
        </form>
      </main>
    </div>
  )
}
