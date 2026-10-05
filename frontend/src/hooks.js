import { useEffect, useRef, useState, useSyncExternalStore } from 'react'

import { countFrame } from './lib/summary'
import { parseHash } from './lib/router'

/** Motion is decoration: anyone who asked the system for less gets none. */
export function prefersReducedMotion() {
  return typeof window !== 'undefined' &&
    window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
}

function subscribeHash(callback) {
  window.addEventListener('hashchange', callback)
  return () => window.removeEventListener('hashchange', callback)
}

/** The current route, re-read whenever the hash changes. */
export function useRoute() {
  const hash = useSyncExternalStore(subscribeHash, () => window.location.hash, () => '')
  return parseHash(hash)
}

export function navigate(href) {
  if (window.location.hash !== href) window.location.hash = href
}

/**
 * Adds `is-visible` to every `.reveal` inside the returned ref as it scrolls
 * into view, once. CSS does the animating.
 */
export function useReveal(deps = []) {
  const ref = useRef(null)
  useEffect(() => {
    const root = ref.current
    if (!root) return undefined
    const items = root.querySelectorAll('.reveal')
    if (prefersReducedMotion() || typeof IntersectionObserver === 'undefined') {
      items.forEach((el) => el.classList.add('is-visible'))
      return undefined
    }
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add('is-visible')
          observer.unobserve(entry.target)
        }
      })
    }, { threshold: 0.15, rootMargin: '0px 0px -40px 0px' })
    items.forEach((el) => observer.observe(el))
    return () => observer.disconnect()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  return ref
}

/**
 * A summary card's text, counting up from zero over `ms` when it first
 * appears. Text-only cards (a people range) are returned as they are.
 */
export function useCountUp(card, ms = 900) {
  const [fraction, setFraction] = useState(() => (prefersReducedMotion() ? 1 : 0))
  useEffect(() => {
    if (typeof card?.value !== 'number' || prefersReducedMotion()) return undefined
    let frame
    const start = performance.now()
    const tick = (now) => {
      const t = Math.min(1, (now - start) / ms)
      setFraction(1 - Math.pow(1 - t, 3))
      if (t < 1) frame = requestAnimationFrame(tick)
    }
    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
  }, [card?.value, ms])
  return countFrame(card, fraction)
}

/** A plain number counting up, for the landing page statistics. */
export function useCountTo(target, { ms = 1200, decimals = 0, start = true } = {}) {
  const [value, setValue] = useState(() => (prefersReducedMotion() ? target : 0))
  useEffect(() => {
    if (!start || prefersReducedMotion()) return undefined
    let frame
    const t0 = performance.now()
    const tick = (now) => {
      const t = Math.min(1, (now - t0) / ms)
      setValue(target * (1 - Math.pow(1 - t, 3)))
      if (t < 1) frame = requestAnimationFrame(tick)
    }
    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
  }, [target, ms, start])
  return value.toFixed(decimals)
}

/** True once the element has scrolled into view (for starting a count). */
export function useInView() {
  const ref = useRef(null)
  const [seen, setSeen] = useState(false)
  useEffect(() => {
    const el = ref.current
    if (!el || seen) return undefined
    if (typeof IntersectionObserver === 'undefined') {
      const id = setTimeout(() => setSeen(true), 0)
      return () => clearTimeout(id)
    }
    const observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) {
        setSeen(true)
        observer.disconnect()
      }
    }, { threshold: 0.3 })
    observer.observe(el)
    return () => observer.disconnect()
  }, [seen])
  return [ref, seen]
}

// ---------------------------------------------------------------- toasts

export const TOAST = 'antardrishti:toast'

/** Show a short message in the corner: toast('Analysis ready', 'ok'). */
export function toast(message, tone = 'info') {
  window.dispatchEvent(new CustomEvent(TOAST, { detail: { message, tone } }))
}

/** The current time, re-rendered every `ms` (one second by default). */
export function useNow(ms = 1000) {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), ms)
    return () => clearInterval(id)
  }, [ms])
  return now
}
