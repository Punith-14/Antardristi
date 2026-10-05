import { useState } from 'react'

import { api } from '../api'
import { hrefFor } from '../lib/router'
import { USER_TYPES, signupProblem } from '../lib/signup'
import Icon from './Icon'
import { AuthAside } from './Login'
import PasswordField from './PasswordField'

const TYPE_ICONS = { ddma: 'building', sdma: 'layers', gis: 'map', researcher: 'search', student: 'sparkle' }

/**
 * Create an account. Whoever signs up gets the account's access at once -
 * no approval step - and is signed straight in. Admin is never offered.
 */
export default function Signup({ onSignedIn, signup }) {
  const types = signup?.user_types?.length ? signup.user_types : USER_TYPES
  const [form, setForm] = useState({ fullName: '', email: '', organisation: '', userType: '', password: '' })
  const [problem, setProblem] = useState(null)
  const [busy, setBusy] = useState(false)
  const [sentTo, setSentTo] = useState('')
  const [resent, setResent] = useState('')

  const set = (key) => (e) => {
    setForm({ ...form, [key]: e.target.value })
    if (problem?.field === key) setProblem(null)
  }

  const submit = async (event) => {
    event.preventDefault()
    const found = signupProblem(form)
    if (found) {
      setProblem(found)
      return
    }
    setBusy(true)
    setProblem(null)
    try {
      const body = await api.signup({
        full_name: form.fullName.trim(), email: form.email.trim(), password: form.password,
        organisation: form.organisation.trim(), user_type: form.userType,
      })
      if (body.verification_sent) setSentTo(body.email)
      else onSignedIn(body.user, { welcome: true })
    } catch (err) {
      setProblem({ field: null, message: err.message })
    } finally {
      setBusy(false)
    }
  }

  const fieldError = (key) => problem?.field === key && <small className="field-error">{problem.message}</small>

  if (sentTo) {
    const resend = async () => {
      try {
        setResent((await api.resendVerification(sentTo)).message)
      } catch (err) {
        setResent(err.message)
      }
    }
    return (
      <div className="auth-page">
        <AuthAside title="One last step" points={['Open the email we just sent', 'Click "Confirm my email"',
          'You will be signed in straight away']} />
        <main className="auth-main">
          <div className="auth-card">
            <div className="mail-art" aria-hidden="true"><Icon name="mail" size={34} /></div>
            <h1>Check your inbox</h1>
            <p className="auth-sub">We sent a confirmation link to <strong>{sentTo}</strong>. It works once and
              expires in 24 hours. Check spam if you do not see it in a minute.</p>
            <button type="button" className="btn btn-outline btn-block" onClick={resend}>Send the link again</button>
            {resent && <p className="auth-notice ok">{resent}</p>}
            <p className="auth-switch">Already confirmed? <a href={hrefFor('login')}>Log in</a></p>
          </div>
        </main>
      </div>
    )
  }

  if (signup && signup.enabled === false) {
    return (
      <div className="auth-page">
        <AuthAside title="Sign-up is closed" points={['Ask your administrator for an account']} />
        <main className="auth-main">
          <div className="auth-card">
            <h1>Sign-up is closed</h1>
            <p className="auth-sub">New accounts are created by an administrator on this server.</p>
            <a className="btn btn-primary btn-block" href={hrefFor('login')}>Log in</a>
          </div>
        </main>
      </div>
    )
  }

  return (
    <div className="auth-page">
      <AuthAside title="Start mapping in a minute"
        points={[signup?.verification ? 'Confirm your email and you are in' : 'Your account works as soon as you sign up',
          'Run flood and land analyses anywhere in India',
          'Download maps, reports and GIS files']} />
      <main className="auth-main">
        <form className={`auth-card auth-card-wide ${problem && !problem.field ? 'shake' : ''}`} onSubmit={submit} noValidate>
          <h1>Create your account</h1>
          <p className="auth-sub">Free, and ready the moment you finish.</p>

          <div className="field-row">
            <label className="field">
              <span>Full name</span>
              <input value={form.fullName} onChange={set('fullName')} autoComplete="name" placeholder="Asha Rao"
                aria-invalid={problem?.field === 'fullName'} autoFocus />
              {fieldError('fullName')}
            </label>
            <label className="field">
              <span>Email</span>
              <input type="email" value={form.email} onChange={set('email')} autoComplete="email"
                placeholder="asha@ddma.gov.in" aria-invalid={problem?.field === 'email'} />
              {fieldError('email')}
            </label>
          </div>
          <label className="field">
            <span>Organisation <em>(optional)</em></span>
            <input value={form.organisation} onChange={set('organisation')} autoComplete="organization"
              placeholder="DDMA Morigaon" />
          </label>

          <fieldset className="field type-field">
            <legend>I am a…</legend>
            <div className="type-grid" role="radiogroup">
              {types.map((t) => (
                <label key={t.key} className={`type-card ${form.userType === t.key ? 'is-on' : ''}`}>
                  <input type="radio" name="userType" value={t.key} checked={form.userType === t.key}
                    onChange={set('userType')} />
                  <Icon name={TYPE_ICONS[t.key] || 'user'} size={18} />
                  <span>{t.label}</span>
                </label>
              ))}
            </div>
            {fieldError('userType')}
          </fieldset>

          <PasswordField value={form.password} onChange={set('password')}
            invalid={problem?.field === 'password'} error={problem?.field === 'password' ? problem.message : ''} />

          {problem && !problem.field && <p className="error" role="alert">{problem.message}</p>}
          <button className="btn btn-primary btn-block" type="submit" disabled={busy}>
            {busy ? <span className="spinner" /> : null}{busy ? 'Creating your account…' : 'Create account'}
          </button>
          <p className="auth-fine">Admin access is given by an existing admin, never at sign-up.</p>
          <p className="auth-switch">Already have an account? <a href={hrefFor('login')}>Log in</a></p>
        </form>
      </main>
    </div>
  )
}
