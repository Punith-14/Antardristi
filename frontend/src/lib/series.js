/**
 * Geometry and wording for the monthly series chart.
 *
 * Bars, not a line. A line drawn through months asserts continuity, and the
 * points it would join are exactly the ones a time series gets wrong: a month
 * with no imagery becomes an interpolated value, or - worse - a drop to zero,
 * which reads as "the flood ended". Separate bars let a missing month be a
 * visibly empty slot labelled as such.
 *
 * Kept out of the component so the rules - a gap is never height zero, a
 * partial month is marked, the axis starts at zero - are tested rather than
 * eyeballed.
 */

/** How to draw a point, most serious first. */
export function pointTone(point) {
  if (!point?.observed) return 'gap'
  const kinds = new Set((point.flags || []).map((f) => f.kind))
  if (kinds.has('sensor_differs') || kinds.has('method_differs') || kinds.has('orbit_differs')) {
    return 'different'
  }
  if (kinds.has('low_coverage')) return 'partial'
  return 'ok'
}

/** The value a bar shows: km² by default, or percent of observed area. */
export function pointValue(point, usePercent = false) {
  if (!point?.observed) return null
  const value = usePercent ? point.fraction?.value : point.value
  return Number.isFinite(value) ? value : null
}

/**
 * A round axis maximum at or above `value`: 1, 2, 2.5, 5, 10 x 10^n.
 *
 * Zero or missing gives 1, so an all-dry series still draws an axis instead
 * of dividing by zero.
 */
export function niceMax(value) {
  if (!Number.isFinite(value) || value <= 0) return 1
  const exponent = Math.pow(10, Math.floor(Math.log10(value)))
  for (const step of [1, 2, 2.5, 5, 10]) {
    if (step * exponent >= value) return step * exponent
  }
  return 10 * exponent
}

/**
 * Bars for an SVG of the given size.
 *
 * Every bar gets a slot, observed or not, so months stay evenly spaced and a
 * missing one is visible as a missing one. The axis always starts at zero:
 * a truncated axis makes a 5% change look like a doubling.
 */
export function chartGeometry(points, options = {}) {
  const {
    width = 320,
    height = 160,
    padLeft = 36,
    padRight = 6,
    padTop = 8,
    padBottom = 22,
    usePercent = false,
  } = options

  const list = Array.isArray(points) ? points : []
  const values = list.map((p) => pointValue(p, usePercent)).filter((v) => v !== null)
  const yMax = niceMax(values.length ? Math.max(...values) : 0)

  const plotWidth = Math.max(width - padLeft - padRight, 1)
  const plotHeight = Math.max(height - padTop - padBottom, 1)
  const slot = list.length ? plotWidth / list.length : plotWidth
  const barWidth = Math.max(slot * 0.7, 1)

  const bars = list.map((point, index) => {
    const value = pointValue(point, usePercent)
    const x = padLeft + index * slot + (slot - barWidth) / 2
    const tone = pointTone(point)

    if (value === null) {
      // A gap is a full-height empty slot, never a zero-height bar. A bar of
      // height zero is a measurement of zero, and this month has none.
      return {
        x, y: padTop, width: barWidth, height: plotHeight,
        value: null, tone: 'gap', label: point?.label ?? '',
        requestId: point?.request_id ?? null, point,
      }
    }

    const barHeight = (value / yMax) * plotHeight
    return {
      x,
      y: padTop + plotHeight - barHeight,
      width: barWidth,
      height: barHeight,
      value,
      tone,
      label: point.label,
      requestId: point.request_id ?? null,
      point,
    }
  })

  const ticks = [0, yMax / 2, yMax].map((value) => ({
    value,
    y: padTop + plotHeight - (value / yMax) * plotHeight,
  }))

  return {
    bars, ticks, yMax, unit: usePercent ? '%' : 'km²',
    baseline: padTop + plotHeight, padLeft, padTop, plotWidth, plotHeight,
  }
}

/** Short month label for the axis: "2018-08" -> "Aug". */
export function monthLabel(label) {
  const match = /^(\d{4})-(\d{2})$/.exec(label || '')
  if (!match) return label || ''
  const names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
    'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
  return names[Number(match[2]) - 1] || label
}

/** One line under the chart saying how much of it can be compared. */
export function summaryLine(series) {
  const points = series?.points || []
  const observed = points.filter((p) => p.observed).length
  const c = series?.comparability || {}

  const parts = [`${observed} of ${points.length} months observed`]
  if (c.partial?.length) parts.push(`${c.partial.length} partly`)
  if (c.differently_measured?.length) {
    parts.push(`${c.differently_measured.length} measured differently`)
  }
  return parts.join(' · ')
}

/** Would the percentage series be the fairer one to look at? */
export function prefersPercent(series) {
  return Boolean(series?.comparability?.partial?.length)
}
