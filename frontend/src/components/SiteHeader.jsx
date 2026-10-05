import { useEffect, useState } from 'react'

import { hrefFor } from '../lib/router'
import Icon from './Icon'

export function Logo({ href = hrefFor('landing'), light = true }) {
  return (
    <a className={`logo ${light ? 'logo-light' : ''}`} href={href} aria-label="Antardrishti home">
      <span className="logo-mark"><img src="/logo-mark.png" alt="" /></span>
      <span className="logo-word">Antardrishti</span>
    </a>
  )
}

const LINKS = [
  { page: 'services', label: 'Services' },
  { page: 'live', label: 'Live satellites' },
  { page: 'how', label: 'How it works' },
  { page: 'accuracy', label: 'Accuracy' },
  { page: 'who', label: "Who it's for" },
]

/** The public top bar: section links, and Log in / Sign up on the right. */
export default function SiteHeader({ active }) {
  const [scrolled, setScrolled] = useState(false)
  const [open, setOpen] = useState(false)

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 12)
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  return (
    <header className={`site-header ${scrolled ? 'is-scrolled' : ''}`}>
      <div className="wrap site-header-inner">
        <Logo />
        <nav className={`site-nav ${open ? 'is-open' : ''}`} aria-label="Sections">
          {LINKS.map((link) => (
            <a key={link.page} href={hrefFor(link.page)} className={active === link.page ? 'active' : ''}
              onClick={() => setOpen(false)}>
              {link.label}
            </a>
          ))}
          <span className="site-nav-auth">
            <a className="btn btn-ghost-light btn-sm" href={hrefFor('login')}>Log in</a>
            <a className="btn btn-primary btn-sm" href={hrefFor('signup')}>Sign up</a>
          </span>
        </nav>
        <div className="site-auth">
          <a className="btn btn-ghost-light btn-sm" href={hrefFor('login')}>Log in</a>
          <a className="btn btn-primary btn-sm" href={hrefFor('signup')}>Sign up</a>
        </div>
        <button type="button" className="menu-toggle" aria-label={open ? 'Close menu' : 'Open menu'}
          aria-expanded={open} onClick={() => setOpen((o) => !o)}>
          <Icon name={open ? 'close' : 'menu'} size={22} />
        </button>
      </div>
    </header>
  )
}
