import { useEffect, useState } from 'react'

import { api } from '../api'
import { toast, useReveal } from '../hooks'
import { passwordProblem } from '../lib/password'
import { relativeTime } from '../lib/summary'
import Icon from './Icon'
import PasswordField from './PasswordField'

const EVENT_LABELS = {
  login: 'Log in', signup: 'Account created', password_changed: 'Password changed',
  password_reset: 'Password reset by email', reset_requested: 'Reset link requested',
  email_verified: 'Email confirmed', signed_out_everywhere: 'Signed out everywhere',
}

/** Your account: who you are, your password, your sessions and recent activity. */
export default function AccountPage({ user, onSignedOut, onUserChange }) {
  const ref = useReveal([])
  const [form, setForm] = useState({ current: '', next: '', confirm: '' })
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [events, setEvents] = useState(null)

  useEffect(() => {
    api.activity().then((body) => setEvents(body.events)).catch(() => setEvents([]))
  }, [])

  const set = (key) => (e) => { setForm({ ...form, [key]: e.target.value }); setError('') }

  const change = async (event) => {
    event.preventDefault()
    if (!form.current) return setError('Enter your current password.')
    const weak = passwordProblem(form.next, [user?.full_name, user?.username])
    if (weak) return setError(weak)
    if (form.next !== form.confirm) return setError('The two new passwords are not the same.')
    setBusy(true)
    try {
      const body = await api.changePassword(form.current, form.next)
      onUserChange?.(body.user)
      setForm({ current: '', next: '', confirm: '' })
      toast('Password changed. Other devices have been signed out.', 'ok')
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const everywhere = async () => {
    if (!window.confirm('Sign out of Antardrishti on every device, including this one?')) return
    await api.logoutEverywhere().catch(() => {})
    onSignedOut?.()
  }

  return (
    <main className="page wrap account-page" ref={ref}>
      <div className="page-head">
        <div>
          <h1>Your account</h1>
          <p>Your details, your password and where your account has been used.</p>
        </div>
      </div>

      <div className="account-grid">
        <section className="card reveal">
          <h3><Icon name="user" size={18} /> Profile</h3>
          <dl className="profile-list">
            <div><dt>Name</dt><dd>{user?.full_name || '—'}</dd></div>
            <div><dt>Email / username</dt><dd>{user?.email || user?.username}</dd></div>
            <div><dt>Organisation</dt><dd>{user?.organisation || '—'}</dd></div>
            <div><dt>I am a</dt><dd>{user?.user_type_label || '—'}</dd></div>
            <div><dt>Access</dt><dd className="cap">{user?.role}</dd></div>
            <div><dt>Member since</dt><dd>{user?.created_at ? user.created_at.slice(0, 10) : '—'}</dd></div>
          </dl>
        </section>

        <form className="card reveal" onSubmit={change}>
          <h3><Icon name="key" size={18} /> Change password</h3>
          <label className="field">
            <span>Current password</span>
            <input type="password" value={form.current} onChange={set('current')} autoComplete="current-password" />
          </label>
          <PasswordField label="New password" value={form.next} onChange={set('next')} id="account-new" />
          <label className="field">
            <span>Type the new password again</span>
            <input type="password" value={form.confirm} onChange={set('confirm')} autoComplete="new-password" />
          </label>
          {error && <p className="error" role="alert">{error}</p>}
          <button className="btn btn-primary" type="submit" disabled={busy}>
            {busy && <span className="spinner" />}{busy ? 'Saving…' : 'Change password'}
          </button>
          <small className="muted block-note">Changing it signs out your other devices; you stay signed in here.</small>
        </form>

        <section className="card reveal">
          <h3><Icon name="shield" size={18} /> Sessions</h3>
          <p className="muted">Lost a phone, or used a shared computer? End every session at once.</p>
          <button type="button" className="btn btn-outline" onClick={everywhere}>
            <Icon name="logout" size={16} /> Sign out everywhere
          </button>
        </section>

        <section className="card reveal activity-card">
          <h3><Icon name="history" size={18} /> Recent activity</h3>
          {events === null && <p className="muted">Loading…</p>}
          {events?.length === 0 && <p className="muted">Nothing yet.</p>}
          <ul className="activity-list">
            {events?.map((e, i) => (
              <li key={`${e.at}-${i}`} className={e.ok ? '' : 'bad'}>
                <span className={`activity-dot ${e.ok ? 'ok' : 'bad'}`} />
                <span>
                  <strong>{EVENT_LABELS[e.kind] || e.kind}{e.ok ? '' : ' (failed)'}</strong>
                  <small>{relativeTime(e.at)}{e.address ? ` · ${e.address}` : ''}{!e.ok && e.detail ? ` · ${e.detail}` : ''}</small>
                </span>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </main>
  )
}
