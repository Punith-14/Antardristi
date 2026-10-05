/**
 * The headline cards above a result, and the number formats they use.
 *
 * Every figure is read from the result as returned - the evidence record,
 * the population block, the district rows - never recomputed here, so a
 * card always says what the report says.
 */

const KM2 = 'km2'

/** 1234567 -> "12,34,567" (Indian grouping). */
export function indianNumber(n) {
  if (typeof n !== 'number' || !Number.isFinite(n)) return '—'
  return Math.round(n).toLocaleString('en-IN')
}

/** 210000 -> "2.1 lakh", 15000000 -> "1.5 crore", 950 -> "950". */
export function compactIndian(n) {
  if (typeof n !== 'number' || !Number.isFinite(n)) return '—'
  const trim = (x) => x.toFixed(1).replace(/\.0$/, '')
  if (Math.abs(n) >= 1e7) return `${trim(n / 1e7)} crore`
  if (Math.abs(n) >= 1e5) return `${trim(n / 1e5)} lakh`
  return indianNumber(n)
}

/** {low: 180000, high: 210000} -> "1.8–2.1 lakh"; one value when they match. */
export function peopleRange(range) {
  if (!range || typeof range.low !== 'number') return null
  const { low, high } = range
  if (typeof high !== 'number' || high === low) return compactIndian(low)
  const lo = compactIndian(low)
  const hi = compactIndian(high)
  const unit = (s) => (s.match(/ (lakh|crore)$/) || [])[1] || ''
  if (unit(lo) && unit(lo) === unit(hi)) return `${lo.replace(/ (lakh|crore)$/, '')}–${hi}`
  // 17,800 to 1.1 lakh reads as two different units; say both in lakh.
  if (unit(hi) === 'lakh' && low >= 1e4) {
    return `${(low / 1e5).toFixed(2).replace(/0$/, '')}–${hi}`
  }
  return `${lo}–${hi}`
}

export function areaText(value) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return `${value.toLocaleString('en-IN', { maximumFractionDigits: value < 10 ? 2 : 1 })} km²`
}

function evidence(result, test) {
  return (result?.evidence || []).find((e) => typeof e.value === 'number' && test(e)) || null
}

const QUANTITY_LABELS = {
  flood_extent: 'Flooded area',
  water_area: 'Water area',
  surface_water_area: 'Water area',
  vegetation_area: 'Healthy vegetation',
  built_up_area: 'Built-up area',
  bare_ground_area: 'Bare ground',
  green_cover_area: 'Green cover',
  stressed_area: 'Stressed vegetation',
}

export function quantityLabel(quantity) {
  if (QUANTITY_LABELS[quantity]) return QUANTITY_LABELS[quantity]
  const text = String(quantity || 'Area').replace(/_/g, ' ')
  return text.charAt(0).toUpperCase() + text.slice(1)
}

/**
 * Up to four cards: {key, label, value, display, format, hint}.
 * `value` is numeric when the card can count up; `display` is the final text.
 */
export function summaryCards(result) {
  if (!result || result.unobserved?.reason === 'no_usable_imagery') return []
  const cards = []

  const area = evidence(result, (e) => e.quantity === 'flood_extent')
    || evidence(result, (e) => e.unit === KM2)
  if (area) {
    cards.push({
      key: 'area', label: quantityLabel(area.quantity), value: area.value,
      display: areaText(area.value), format: 'km2', hint: `Evidence ${area.id}`,
    })
  }

  const people = result.population?.people_in_flood
  const peopleText = peopleRange(people)
  if (peopleText) {
    cards.push({
      key: 'people', label: 'People living there', value: null,
      display: peopleText, format: 'text', hint: 'Two population models; the range is honest',
    })
  }

  const rows = result.districts?.rows
  if (Array.isArray(rows) && rows.length) {
    const hit = rows.filter((r) => (r.flooded_km2 ?? 0) > 0).length
    cards.push({
      key: 'districts', label: hit === 1 ? 'District affected' : 'Districts affected',
      value: hit, display: String(hit), format: 'int', hint: `${rows.length} checked`,
    })
  } else if (typeof result.observation?.coverage_fraction === 'number') {
    const pct = Math.round(result.observation.coverage_fraction * 100)
    cards.push({
      key: 'coverage', label: 'Area seen', value: pct, display: `${pct}%`,
      format: 'pct', hint: 'Of the region, by the satellite',
    })
  }

  const used = result.observation?.scenes_used
  if (typeof used === 'number') {
    cards.push({
      key: 'images', label: used === 1 ? 'Image used' : 'Images used', value: used,
      display: String(used), format: 'int', hint: result.observation?.sensor_used || '',
    })
  }
  return cards.slice(0, 4)
}

/** The text a counting card shows at a fraction of its final value. */
export function countFrame(card, fraction) {
  if (typeof card?.value !== 'number') return card?.display ?? ''
  const f = Math.min(1, Math.max(0, fraction))
  if (f >= 1) return card.display
  const v = card.value * f
  if (card.format === 'km2') return areaText(v)
  if (card.format === 'pct') return `${Math.round(v)}%`
  return String(Math.round(v))
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

function parts(iso) {
  const m = String(iso || '').match(/^(\d{4})-(\d{2})-(\d{2})/)
  return m ? { y: m[1], m: MONTHS[Number(m[2]) - 1], d: String(Number(m[3])) } : null
}

/** {start: '2026-07-01', end: '2026-07-10'} -> "1–10 Jul 2026". */
export function periodText(period) {
  const a = parts(period?.start)
  const b = parts(period?.end)
  if (!a) return ''
  if (!b) return `${a.d} ${a.m} ${a.y}`
  if (a.y === b.y && a.m === b.m) return a.d === b.d ? `${a.d} ${a.m} ${a.y}` : `${a.d}–${b.d} ${a.m} ${a.y}`
  if (a.y === b.y) return `${a.d} ${a.m} – ${b.d} ${b.m} ${a.y}`
  return `${a.d} ${a.m} ${a.y} – ${b.d} ${b.m} ${b.y}`
}

/** An ISO time -> "just now", "12 min ago", "3 h ago", "yesterday", or a date. */
export function relativeTime(iso, now = Date.now()) {
  const t = Date.parse(iso)
  if (!Number.isFinite(t)) return ''
  const s = Math.max(0, (now - t) / 1000)
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)} min ago`
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`
  if (s < 172800) return 'yesterday'
  return periodText({ start: new Date(t).toISOString().slice(0, 10) })
}
