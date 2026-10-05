/**
 * Which page the address bar points at.
 *
 * Hash routes (#/app/new) rather than paths: the API serves the built app as
 * static files, and a hash never reaches the server, so a refresh on any page
 * still loads index.html instead of a 404.
 *
 *   #/                 landing (public)        #/app          home dashboard
 *   #/services ...     a landing section       #/app/new      new analysis
 *   #/login            log in                  #/app/ask      ask a question
 *   #/signup           sign up                 #/app/history  past analyses
 *   #/forgot, #/reset?token=, #/verify?token=   password reset, email confirmation
 *                                              #/app/account  your account
 *                                              #/app/satellites live satellites
 *                                              #/app/help     how it works
 *                                              #/app/users    users (admin)
 */

export const PUBLIC_PAGES = ['landing', 'login', 'signup', 'forgot', 'reset', 'verify']
const AUTH_PAGES = ['login', 'signup', 'forgot', 'reset', 'verify']
export const APP_PAGES = ['home', 'new', 'ask', 'history', 'satellites', 'help', 'users', 'account']
export const LANDING_SECTIONS = ['services', 'live', 'how', 'accuracy', 'who']

/** The top-bar links once signed in, in order. Users is added for admins. */
export const APP_NAV = [
  { page: 'home', label: 'Home' },
  { page: 'new', label: 'New analysis' },
  { page: 'ask', label: 'Ask' },
  { page: 'history', label: 'History' },
  { page: 'satellites', label: 'Satellites' },
  { page: 'help', label: 'Help' },
]

export function parseHash(hash) {
  const raw = String(hash || '').replace(/^#/, '')
  const [path, query = ''] = raw.split('?')
  const params = Object.fromEntries(new URLSearchParams(query))
  const parts = path.split('/').filter(Boolean)
  if (!parts.length) return { page: 'landing', section: null, params }
  if (AUTH_PAGES.includes(parts[0])) return { page: parts[0], section: null, params }
  if (parts[0] === 'app') {
    const page = parts[1] || 'home'
    return { page: APP_PAGES.includes(page) ? page : 'home', section: null, params }
  }
  if (LANDING_SECTIONS.includes(parts[0])) return { page: 'landing', section: parts[0], params }
  return { page: 'landing', section: null, params }
}

export function hrefFor(page, params = {}) {
  const query = new URLSearchParams(
    Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== ''),
  ).toString()
  const tail = query ? `?${query}` : ''
  if (page === 'landing') return `#/${tail}`
  if (LANDING_SECTIONS.includes(page)) return `#/${page}${tail}`
  if (AUTH_PAGES.includes(page)) return `#/${page}${tail}`
  if (page === 'home') return `#/app${tail}`
  return `#/app/${page}${tail}`
}

export const isAppPage = (page) => APP_PAGES.includes(page)

/**
 * Where a route should really go, given the session, or null to stay.
 *
 * A signed-out visitor asking for an app page goes to log in and comes back
 * afterwards; a signed-in one asking for log in or sign up goes home.
 * With sign-in switched off (authRequired false) every page is open.
 */
export function redirectFor(route, { user, authRequired, isAdmin = false }) {
  const signedIn = Boolean(user) || !authRequired
  if (isAppPage(route.page) && !signedIn) {
    return { page: 'login', params: { next: route.page } }
  }
  if (['login', 'signup', 'forgot'].includes(route.page) && signedIn && authRequired) {
    return { page: APP_PAGES.includes(route.params?.next) ? route.params.next : 'home', params: {} }
  }
  if (route.page === 'users' && !isAdmin) return { page: 'home', params: {} }
  return null
}
