import { useState } from 'react'

import { api } from '../api'
import { loginProblem } from '../lib/session'

/** Sign-in. The session is an HttpOnly cookie the server sets; nothing is stored here. */
export default function Login({ onSignedIn, notice }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async (event) => {
    event.preventDefault()
    const problem = loginProblem(username, password)
    if (problem) {
      setError(problem)
      return
    }
    setBusy(true)
    setError('')
    try {
      const { user } = await api.login(username.trim(), password)
      setPassword('')
      onSignedIn(user)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-page">
      <form className="login-card" onSubmit={submit}>
        <img src="/logo.png" alt="" className="login-logo" onError={(e) => { e.currentTarget.style.display = 'none' }} />
        <h1>Antardrishti</h1>
        <p className="login-tagline">Earth observation for India, with every number traceable</p>
        {notice && <p className="login-notice">{notice}</p>}
        <label className="field">
          <span>Username</span>
          <input autoComplete="username" value={username} autoFocus
            onChange={(e) => setUsername(e.target.value)} />
        </label>
        <label className="field">
          <span>Password</span>
          <input type="password" autoComplete="current-password" value={password}
            onChange={(e) => setPassword(e.target.value)} />
        </label>
        {error && <p className="error" role="alert">{error}</p>}
        <button className="run" type="submit" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
        <small className="login-foot">Accounts are created by your administrator.</small>
      </form>
    </div>
  )
}
