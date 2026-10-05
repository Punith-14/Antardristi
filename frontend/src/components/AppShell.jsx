import { useEffect, useRef, useState } from 'react'

import { TOAST } from '../hooks'
import { APP_NAV, hrefFor } from '../lib/router'
import { firstName, initials } from '../lib/signup'
import Icon from './Icon'
import { Logo } from './SiteHeader'

const NAV_ICONS = { home: 'home', new: 'plus', ask: 'chat', history: 'history', satellites: 'satellite', help: 'help', users: 'users' }

/** Short messages in the corner that fade away by themselves. */
function Toasts() {
  const [items, setItems] = useState([])
  const next = useRef(1)
  useEffect(() => {
    const onToast = (event) => {
      const id = next.current++
      setItems((list) => [...list.slice(-2), { id, ...event.detail }])
      setTimeout(() => setItems((list) => list.filter((t) => t.id !== id)), 4200)
    }
    window.addEventListener(TOAST, onToast)
    return () => window.removeEventListener(TOAST, onToast)
  }, [])
  return (
    <div className="toasts" role="status" aria-live="polite">
      {items.map((t) => (
        <div key={t.id} className={`toast toast-${t.tone}`}>
          <Icon name={t.tone === 'error' ? 'help' : 'check'} size={18} />
          <span>{t.message}</span>
        </div>
      ))}
    </div>
  )
}

function roleLine(user) {
  if (!user) return 'Sign-in is off'
  const role = user.role.charAt(0).toUpperCase() + user.role.slice(1)
  return user.user_type_label ? `${user.user_type_label} · ${role}` : role
}

/** The signed-in frame: top bar with the pages, the user menu, and toasts. */
export default function AppShell({ user, page, admin, onSignOut, busy, children }) {
  const [menu, setMenu] = useState(false)
  const nav = admin ? [...APP_NAV, { page: 'users', label: 'Users' }] : APP_NAV

  useEffect(() => {
    if (!menu) return undefined
    const close = () => setMenu(false)
    window.addEventListener('click', close)
    return () => window.removeEventListener('click', close)
  }, [menu])

  return (
    <div className="shell">
      <header className="app-bar">
        <div className="app-bar-inner">
          <Logo href={hrefFor('home')} />
          <nav className="app-nav" aria-label="Pages">
            {nav.map((item) => (
              <a key={item.page} href={hrefFor(item.page)} className={page === item.page ? 'active' : ''}
                aria-current={page === item.page ? 'page' : undefined}>
                <Icon name={NAV_ICONS[item.page]} size={17} />
                <span>{item.label}</span>
                {item.page === 'new' && busy && <i className="nav-busy" title="An analysis is running" />}
              </a>
            ))}
          </nav>
          <div className="user-chip" onClick={(e) => { e.stopPropagation(); setMenu((m) => !m) }}
            role="button" tabIndex={0} aria-haspopup="menu" aria-expanded={menu}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setMenu((m) => !m) } }}>
            <div className="user-text">
              <strong>{user ? firstName(user) : 'Guest'}</strong>
              <small>{roleLine(user)}</small>
            </div>
            <span className="avatar">{user ? initials(user) : '?'}</span>
            {menu && (
              <div className="user-menu-pop" role="menu">
                <div className="user-menu-head">
                  <strong>{user?.full_name || user?.username || 'Guest'}</strong>
                  <small>{user?.email || user?.username}</small>
                  {user?.organisation && <small>{user.organisation}</small>}
                </div>
                <a role="menuitem" href={hrefFor('account')}><Icon name="user" size={16} /> Your account</a>
                <a role="menuitem" href={hrefFor('history')}><Icon name="history" size={16} /> Your analyses</a>
                {admin && <a role="menuitem" href={hrefFor('users')}><Icon name="users" size={16} /> Manage users</a>}
                <a role="menuitem" href={hrefFor('help')}><Icon name="help" size={16} /> Help</a>
                {user && (
                  <button type="button" role="menuitem" onClick={onSignOut}><Icon name="logout" size={16} /> Sign out</button>
                )}
              </div>
            )}
          </div>
        </div>
      </header>
      {children}
      <Toasts />
    </div>
  )
}
