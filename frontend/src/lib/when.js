/** Time phrases for the satellite views, kept apart from orbit.js so they need no SGP4. */

/** "2 h 10 min", "4 min", "1 d 3 h"; "now" under a minute; past times say "ago". */
export function countdown(ms) {
  if (!Number.isFinite(ms)) return '—'
  const past = ms < 0
  const s = Math.abs(ms) / 1000
  if (s < 60) return past ? 'just now' : 'now'
  const d = Math.floor(s / 86400)
  const h = Math.floor((s % 86400) / 3600)
  const m = Math.floor((s % 3600) / 60)
  const text = d ? `${d} d ${h} h` : h ? `${h} h ${m} min` : `${m} min`
  return past ? `${text} ago` : `in ${text}`
}

/** An ISO UTC time in India Standard Time: "3 Oct, 06:12 IST". */
export function istTime(iso) {
  const t = iso instanceof Date ? iso : new Date(iso)
  if (!Number.isFinite(t.getTime())) return '—'
  const ist = new Date(t.getTime() + 5.5 * 3600000)
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
  const hh = String(ist.getUTCHours()).padStart(2, '0')
  const mm = String(ist.getUTCMinutes()).padStart(2, '0')
  return `${ist.getUTCDate()} ${months[ist.getUTCMonth()]}, ${hh}:${mm} IST`
}

