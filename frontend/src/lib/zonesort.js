/**
 * Ordering for the zone table.
 *
 * Pulled out of the component because sorting is where silent wrongness
 * lives: a comparator that returns a boolean instead of a number sorts
 * almost right, a severity sort that compares strings puts "low" above
 * "moderate" alphabetically, and neither looks broken at a glance.
 */

/** Worst first. Alphabetical order would read low, moderate, high. */
export const SEVERITY_ORDER = { high: 0, moderate: 1, low: 2 }

export const SORTS = [
  { key: 'rank', label: 'Rank' },
  { key: 'area', label: 'Area' },
  { key: 'severity', label: 'Severity' },
  { key: 'north', label: 'Latitude' },
]

function value(zone, key) {
  switch (key) {
    case 'area':
      return zone?.area_km2 ?? 0
    case 'severity':
      return SEVERITY_ORDER[zone?.severity] ?? 99
    case 'north':
      return zone?.centroid?.[1] ?? 0
    case 'rank':
    default:
      return zone?.rank ?? 0
  }
}

/**
 * A sorted copy. Never sorts in place - the caller's array is the response
 * object, and mutating it would reorder the map's zone list too.
 *
 * `direction` is 'asc' or 'desc'. Ties fall back to rank so the order is
 * stable and repeatable: without it, two equal-severity zones can swap
 * places between renders for no visible reason.
 */
export function sortZones(zones, key = 'rank', direction = 'asc') {
  if (!Array.isArray(zones)) return []

  const sign = direction === 'desc' ? -1 : 1
  return [...zones].sort((a, b) => {
    const difference = value(a, key) - value(b, key)
    if (difference !== 0) return difference * sign
    return (a?.rank ?? 0) - (b?.rank ?? 0)
  })
}

/**
 * Which direction a column should start in when first clicked.
 *
 * Rank ascending means "biggest first", because rank 1 IS the biggest. Area
 * ascending would mean "smallest first", which nobody wants from one click.
 */
export function defaultDirection(key) {
  return key === 'area' ? 'desc' : 'asc'
}

/** Clicking the active column flips it; clicking another switches to it. */
export function nextSort(current, key) {
  if (current.key !== key) return { key, direction: defaultDirection(key) }
  return { key, direction: current.direction === 'asc' ? 'desc' : 'asc' }
}
