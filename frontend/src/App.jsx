import { Suspense, lazy, useCallback, useEffect, useState } from 'react'

import { SIGNED_OUT, api } from './api'
import { navigate, toast, useRoute } from './hooks'
import { hrefFor, isAppPage, redirectFor } from './lib/router'
import { canRun, isAdmin } from './lib/session'
import { firstName } from './lib/signup'
import AccountPage from './components/AccountPage'
import AdminUsers from './components/AdminUsers'
import { ForgotPage, ResetPage, VerifyPage } from './components/AuthExtra'
import AppShell from './components/AppShell'
import HelpPage from './components/HelpPage'
import HistoryPage from './components/HistoryPage'
import Home from './components/Home'
import Landing from './components/Landing'
import Login from './components/Login'
import Signup from './components/Signup'
import Workspace from './components/Workspace'

// Three.js and the orbit model load only when the Satellites page is opened.
const SatellitesPage = lazy(() => import('./components/SatellitesPage'))
import './App.css'
import './site.css'

function Splash() {
  return (
    <div className="splash" role="status">
      <img src="/logo-mark.png" alt="" />
      <span>Antardrishti</span>
    </div>
  )
}

/**
 * Who is signed in, and which page the address points at, decide what is
 * shown. The landing page, log in and sign up are public; everything under
 * #/app needs a session (unless sign-in is switched off on the server).
 */
export default function App() {
  const route = useRoute()
  const [session, setSession] = useState({ loading: true })
  const [notice, setNotice] = useState('')

  useEffect(() => {
    api.me()
      .then((body) => setSession({ user: body.user, authRequired: body.auth_required, signup: body.signup }))
      .catch((err) => setSession({ user: null, authRequired: true, offline: err.message }))
    const onSignedOut = () => {
      setNotice('Your session ended. Please log in again.')
      setSession((s) => ({ ...s, user: null }))
    }
    window.addEventListener(SIGNED_OUT, onSignedOut)
    return () => window.removeEventListener(SIGNED_OUT, onSignedOut)
  }, [])

  const admin = isAdmin(session.user, session.authRequired)
  const redirect = session.loading ? null
    : redirectFor(route, { user: session.user, authRequired: session.authRequired, isAdmin: admin })
  const target = redirect ? hrefFor(redirect.page, redirect.params) : null

  useEffect(() => {
    if (target) navigate(target)
  }, [target])

  const next = route.params?.next
  const signedIn = useCallback((user, { welcome = false, message = '' } = {}) => {
    setNotice('')
    setSession((s) => ({ ...s, user, authRequired: true }))
    toast(message || (welcome ? `Welcome to Antardrishti, ${firstName(user)}` : `Welcome back, ${firstName(user)}`), 'ok')
    navigate(hrefFor(isAppPage(next) ? next : 'home'))
  }, [next])

  const signOut = useCallback(async () => {
    await api.logout().catch(() => {})
    setSession((s) => ({ ...s, user: null }))
    navigate(hrefFor('landing'))
  }, [])

  if (session.loading || target) return <Splash />

  if (route.page === 'landing') return <Landing section={route.section} />
  if (route.page === 'login') {
    return (
      <Login
        notice={notice || (session.offline ? `Cannot reach the server (${session.offline}).` : '')}
        onSignedIn={signedIn}
      />
    )
  }
  if (route.page === 'signup') return <Signup signup={session.signup} onSignedIn={signedIn} />
  if (route.page === 'forgot') return <ForgotPage signup={session.signup} />
  if (route.page === 'reset') return <ResetPage token={route.params.token} onSignedIn={signedIn} />
  if (route.page === 'verify') return <VerifyPage token={route.params.token} onSignedIn={signedIn} />

  return (
    <SignedIn key={session.user?.username || 'open'} session={session} route={route} admin={admin}
      onSignOut={signOut} onUserChange={(user) => setSession((s) => ({ ...s, user }))} />
  )
}

/** Recent sign-ins across every account, for the admin. */
function AdminLogins() {
  const [events, setEvents] = useState(null)
  useEffect(() => { api.adminLogins().then((b) => setEvents(b.events)).catch(() => setEvents([])) }, [])
  return (
    <div className="card table-card admin-logins">
      <h3>Recent sign-ins</h3>
      {events === null ? <p className="muted">Loading…</p> : (
        <table className="history-table">
          <thead><tr><th>When</th><th>Who</th><th>What</th><th>Result</th><th>Address</th></tr></thead>
          <tbody>
            {events.map((e, i) => (
              <tr key={`${e.at}-${i}`}>
                <td>{e.at?.replace('T', ' ').replace('Z', ' UTC')}</td><td>{e.username}</td><td>{e.kind}</td>
                <td className={e.ok ? 'ok-text' : 'bad-text'}>{e.ok ? 'OK' : `Failed${e.detail ? `: ${e.detail}` : ''}`}</td>
                <td>{e.address || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

/** Everything after logging in. The workspace stays mounted across pages. */
function SignedIn({ session, route, admin, onSignOut, onUserChange }) {
  const { user, authRequired } = session
  const mayRun = canRun(user, authRequired)
  const [catalogue, setCatalogue] = useState(null)
  const [catalogueError, setCatalogueError] = useState('')
  const [busy, setBusy] = useState(false)
  const page = route.page
  const inWorkspace = page === 'new' || page === 'ask'

  useEffect(() => {
    api.catalogue()
      .then(setCatalogue)
      .catch((err) => setCatalogueError(`Cannot reach the server at ${api.base || 'this address'} (${err.message}).`))
  }, [])

  // Each page starts at the top, the way a real site does.
  useEffect(() => { window.scrollTo(0, 0) }, [page])

  return (
    <AppShell user={user} page={page} admin={admin} onSignOut={onSignOut} busy={busy}>
      <Workspace
        mode={page === 'ask' ? 'ask' : 'new'}
        params={inWorkspace ? route.params : {}}
        visible={inWorkspace}
        catalogue={catalogue}
        catalogueError={catalogueError}
        mayRun={mayRun}
        onBusy={setBusy}
      />
      {page === 'home' && <Home user={user} mayRun={mayRun} />}
      {page === 'history' && <HistoryPage />}
      {page === 'satellites' && (
        <Suspense fallback={<main className="page wrap"><div className="table-loading"><span className="spinner dark" /> Loading the satellites…</div></main>}>
          <SatellitesPage mayRun={mayRun} />
        </Suspense>
      )}
      {page === 'help' && <HelpPage catalogue={catalogue} />}
      {page === 'account' && <AccountPage user={user} onSignedOut={onSignOut} onUserChange={onUserChange} />}
      {page === 'users' && admin && (
        <main className="page wrap">
          <div className="page-head">
            <div>
              <h1>Users</h1>
              <p>Everyone with an account. New sign-ups get analyst access at once; only an admin can make another admin.</p>
            </div>
          </div>
          <div className="card help-card"><AdminUsers me={user} inline /></div>
          <AdminLogins />
        </main>
      )}
    </AppShell>
  )
}
