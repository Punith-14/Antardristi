/**
 * The password rules as the forms show them: a live checklist that ticks as
 * the person types. The server (backend/core/passwords.py) checks the same
 * rules again and has the final word, including the full common-password list.
 */

export const MIN_LENGTH = 8

export const RULES = [
  { key: 'length', label: `At least ${MIN_LENGTH} characters` },
  { key: 'upper', label: 'A capital letter (A-Z)' },
  { key: 'lower', label: 'A small letter (a-z)' },
  { key: 'digit', label: 'A number (0-9)' },
  { key: 'symbol', label: 'A symbol, such as ! @ # $ %' },
]

// The most guessed base words; the server's list is longer.
const COMMON = new Set([
  'password', 'passw', 'admin', 'welcome', 'letmein', 'qwerty', 'qwertyuiop', 'asdfgh', 'iloveyou',
  'monkey', 'dragon', 'master', 'login', 'abc', 'abcd', 'abcdef', 'test', 'user', 'secret', 'india',
  'bharat', 'mumbai', 'delhi', 'cricket', 'sachin', 'krishna', 'ganesh', 'flood', 'satellite',
  'antardrishti', 'student', 'college', 'project', 'changeme', 'default', 'hello', 'summer', 'love',
])

export function checks(password) {
  const p = String(password || '')
  return {
    length: p.length >= MIN_LENGTH,
    upper: /[A-Z]/.test(p),
    lower: /[a-z]/.test(p),
    digit: /\d/.test(p),
    symbol: /[^A-Za-z0-9\s]/.test(p),
  }
}

export function baseWord(password) {
  return String(password || '').toLowerCase().replace(/[^a-z]/g, '')
}

export function isCommon(password) {
  const lowered = String(password || '').toLowerCase()
  return COMMON.has(lowered) || COMMON.has(baseWord(password)) || new Set(lowered).size <= 2
}

/** The first reason a password is refused, in words, or null. */
export function passwordProblem(password, personal = []) {
  const p = String(password || '')
  if (!p) return 'Choose a password.'
  const passed = checks(p)
  const failed = RULES.find((r) => !passed[r.key])
  if (failed) return `The password needs ${failed.label.charAt(0).toLowerCase()}${failed.label.slice(1)}.`
  if (isCommon(p)) return 'That password is too common and easy to guess. Try a short sentence with a number and a symbol.'
  const lowered = p.toLowerCase()
  for (const item of personal) {
    for (const part of String(item || '').toLowerCase().split(/[\s@._-]+/)) {
      if (part.length >= 3 && lowered.includes(part)) return 'The password must not contain your name, username or email.'
    }
  }
  return null
}

/** 0-4 rules-met score for the bar under the checklist. */
export function strength(password) {
  const passed = checks(password)
  const met = Object.values(passed).filter(Boolean).length
  if (!password) return 0
  if (met < 5 || isCommon(password)) return Math.min(2, Math.floor(met / 2))
  return String(password).length >= 14 ? 4 : 3
}
