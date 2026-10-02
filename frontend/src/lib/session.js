/** Who is signed in, and what their role lets the interface offer. */

export const ROLE_RANK = { viewer: 0, analyst: 1, admin: 2 }

export function can(user, role, authRequired = true) {
  if (!authRequired) return true
  return Boolean(user) && (ROLE_RANK[user.role] ?? -1) >= ROLE_RANK[role]
}

export const canRun = (user, authRequired) => can(user, 'analyst', authRequired)
export const isAdmin = (user, authRequired) => can(user, 'admin', authRequired)

/** What a viewer sees in place of the run buttons. */
export const VIEW_ONLY = 'Your account can view results. Ask an administrator for analyst access to run analyses.'

export function loginProblem(username, password) {
  if (!username?.trim()) return 'Enter your username.'
  if (!password) return 'Enter your password.'
  return null
}
