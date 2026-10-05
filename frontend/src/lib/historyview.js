/**
 * How a History row (from GET /history, built by backend core/history.py)
 * reads on a card or in the table.
 */
import { hrefFor } from './router.js'
import { areaText } from './summary.js'

/** The headline figure as text: "684.2 km²". */
export function headlineText(item) {
  const h = item?.headline
  if (!h || typeof h.value !== 'number') return null
  return h.unit === 'km2' ? areaText(h.value) : `${h.value} ${h.unit || ''}`.trim()
}

/** Opening a past analysis loads its stored job result - nothing is recomputed. */
export const openHref = (item) => hrefFor('new', { job: item.job_id })

export function titleOf(item) {
  if (item?.saved && item.title) return item.title
  if (item?.kind === 'ask' && item.question) return item.question
  return item?.place || 'Drawn area'
}

/** The History page filters: all, floods, other analyses, questions, monthly. */
export function matchesFilter(item, filter, query) {
  const isFlood = item.kind === 'analyze' || (item.kind === 'ask' && item.analysis === 'Flood extent')
  if (filter === 'flood' && !isFlood) return false
  if (filter === 'other' && item.kind !== 'surface') return false
  if (filter === 'ask' && item.kind !== 'ask') return false
  if (filter === 'series' && item.kind !== 'series') return false
  if (filter === 'saved' && !item.saved) return false
  if (!query) return true
  const text = `${item.place || ''} ${item.question || ''} ${item.analysis || ''} ${item.title || ''} ${item.note || ''}`.toLowerCase()
  return text.includes(String(query).toLowerCase().trim())
}

/** "Kept" for a saved one; "Removed in 5 days" for a recent one. */
export function keepText(item, now = Date.now()) {
  if (item?.saved) return 'Saved'
  const t = Date.parse(item?.expire_at)
  if (!Number.isFinite(t)) return ''
  const days = Math.ceil((t - now) / 86400000)
  if (days <= 0) return 'Removed soon'
  return days === 1 ? 'Removed tomorrow' : `Removed in ${days} days`
}
