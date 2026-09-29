/**
 * Geometry for the before/after swipe.
 *
 * Pulled out of MapView because this is the part that can be wrong in ways a
 * screenshot will not show: an off-by-one at the edges, a divider that drifts
 * when the window is resized, a clip that reveals the wrong side. Pure
 * functions, no DOM, no Leaflet - so `node --test` can check them.
 */

export const MIN_PERCENT = 0
export const MAX_PERCENT = 100

/** Keyboard nudge, in percent. Shift-arrow moves by COARSE_STEP. */
export const STEP = 2
export const COARSE_STEP = 10

export function clampPercent(value) {
  // NaN and Infinity are not the same failure. NaN carries no information, so
  // the divider goes back to the middle. Infinity means the pointer went off
  // an edge, and the ordinary clamp already puts that at the right end -
  // treating it as "no information" would snap the divider to centre mid-drag.
  const number = Number(value)
  if (Number.isNaN(number)) return 50
  return Math.min(MAX_PERCENT, Math.max(MIN_PERCENT, number))
}

/**
 * Where the pointer sits along the map, as a percentage of its width.
 *
 * `rect` is a DOMRect (or anything with left and width). A zero-width rect
 * happens when the map is hidden - a display:none container reports 0 - and
 * dividing by it would yield Infinity and throw the divider off screen.
 */
export function percentFromPointer(clientX, rect) {
  if (!rect || !rect.width) return 50
  return clampPercent(((clientX - rect.left) / rect.width) * 100)
}

/**
 * CSS clip-path hiding everything LEFT of `percent`.
 *
 * The "after" layer sits on top and is clipped, so dragging the divider left
 * reveals more of it. inset() takes its offsets as top/right/bottom/left, so
 * the left inset is the one that moves.
 */
export function clipInset(percent) {
  return `inset(0 0 0 ${clampPercent(percent)}%)`
}

/**
 * Next position for an arrow key, or null if the key is not a swipe key.
 *
 * Returning null rather than the unchanged value matters: the caller uses it to
 * decide whether to preventDefault, and swallowing Tab or Enter would trap
 * keyboard users on the handle.
 */
export function stepFromKey(key, current, shiftKey = false) {
  const distance = shiftKey ? COARSE_STEP : STEP

  switch (key) {
    case 'ArrowLeft':
    case 'ArrowDown':
      return clampPercent(current - distance)
    case 'ArrowRight':
    case 'ArrowUp':
      return clampPercent(current + distance)
    case 'Home':
      return MIN_PERCENT
    case 'End':
      return MAX_PERCENT
    default:
      return null
  }
}

/**
 * Which two tile layers the swipe compares, from a response's artifacts.
 *
 * The "after" layer is whichever overlay this analysis produced - flood,
 * surface or change. The "before" layer only exists in comparison mode, so
 * `enabled` is false for a single-window analysis and the map draws one layer
 * with no divider.
 */
export function swipeLayers(artifacts) {
  const a = artifacts || {}
  const after = a.flood_tiles || a.surface_tiles || a.change_tiles || null
  const before = a.baseline_tiles || null
  return { before, after, enabled: Boolean(before && after) }
}

/**
 * Label for a period, e.g. { start: "2018-08-01", end: "2018-08-31" }.
 *
 * Collapses a window inside one month to that month, because "Aug 2018" reads
 * better on a map than "1 Aug - 31 Aug 2018" and the exact days are already in
 * the evidence record.
 */
export function periodLabel(period) {
  if (!period?.start) return ''

  const format = (iso) => {
    const date = new Date(`${iso}T00:00:00Z`)
    if (Number.isNaN(date.getTime())) return iso
    return date.toLocaleDateString('en-GB', {
      month: 'short',
      year: 'numeric',
      timeZone: 'UTC',
    })
  }

  const from = format(period.start)
  const to = period.end ? format(period.end) : from
  return from === to ? from : `${from} – ${to}`
}
