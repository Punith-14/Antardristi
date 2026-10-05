/**
 * The sign-up form's checks, run before anything is sent so the reason is
 * shown next to the field. The server checks the same things again.
 */

import { MIN_LENGTH, passwordProblem } from './password.js'

export const PASSWORD_MIN = MIN_LENGTH

/** Used when the server cannot be asked (it sends the same list in /auth/me). */
export const USER_TYPES = [
  { key: 'ddma', label: 'District official (DDMA)' },
  { key: 'sdma', label: 'State official (SDMA)' },
  { key: 'gis', label: 'GIS analyst' },
  { key: 'researcher', label: 'Researcher' },
  { key: 'student', label: 'Student' },
]

const EMAIL = /^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$/

/** {field, message} for the first problem, or null. */
export function signupProblem({ fullName, email, password, userType } = {}) {
  if (!String(fullName || '').trim()) return { field: 'fullName', message: 'Enter your name.' }
  if (!EMAIL.test(String(email || '').trim())) {
    return { field: 'email', message: "That doesn't look like an email address." }
  }
  if (!userType) return { field: 'userType', message: 'Choose what you are, so we can set up your account.' }
  const weak = passwordProblem(password, [fullName, email])
  if (weak) return { field: 'password', message: weak }
  return null
}

/** First name for the welcome line: "Asha Rao" -> "Asha"; falls back to the username. */
export function firstName(user) {
  const name = String(user?.full_name || '').trim()
  if (name) return name.split(/\s+/)[0]
  const username = String(user?.username || '')
  const base = username.includes('@') ? username.split('@')[0] : username
  return base ? base.charAt(0).toUpperCase() + base.slice(1) : 'there'
}

export function initials(user) {
  const name = String(user?.full_name || '').trim()
  if (name) return name.split(/\s+/).slice(0, 2).map((w) => w[0].toUpperCase()).join('')
  return (String(user?.username || '?')[0] || '?').toUpperCase()
}
