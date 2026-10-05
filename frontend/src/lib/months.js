/**
 * Months for "Month by month", and the date checks every mode shares.
 *
 * Month by month showed two full date boxes, so you were asked for days that
 * did not matter, and a stale end date (25 Aug 2018, left by the Kerala
 * example) sat before a 2026 start with nothing to say so.
 */

export const MAX_MONTHS = 12

/** '2026-07-20' -> '2026-07' (what an <input type="month"> holds). */
export function monthOf(iso) {
  return /^\d{4}-\d{2}/.test(iso || '') ? iso.slice(0, 7) : ''
}

/** '2026-07' -> '2026-07-01' */
export function firstDay(month) {
  return month ? `${month}-01` : ''
}

/** '2026-02' -> '2026-02-28', '2024-02' -> '2024-02-29' */
export function lastDay(month) {
  if (!month) return ''
  const [y, m] = month.split('-').map(Number)
  const day = new Date(Date.UTC(y, m, 0)).getUTCDate()
  return `${month}-${String(day).padStart(2, '0')}`
}

/** Months from start to end inclusive: '2026-05'..'2026-09' -> 5. */
export function monthsBetween(startMonth, endMonth) {
  if (!startMonth || !endMonth) return 0
  const [ys, ms] = startMonth.split('-').map(Number)
  const [ye, me] = endMonth.split('-').map(Number)
  return (ye - ys) * 12 + (me - ms) + 1
}

/** Dates widened to whole months, for switching into Month by month. */
export function wholeMonths(start, end) {
  return { postStart: firstDay(monthOf(start)), postEnd: lastDay(monthOf(end)) }
}

/** What is wrong with the dates, in words, or null. */
export function dateProblem({ postStart, postEnd, preStart, preEnd }, mode) {
  if (!postStart || !postEnd) return mode === 'monthly' ? 'Choose both months.' : 'Choose both dates.'
  if (postStart > postEnd) {
    return mode === 'monthly'
      ? 'The first month is after the last month.'
      : 'The start date is after the end date.'
  }
  if (mode === 'monthly' && monthsBetween(monthOf(postStart), monthOf(postEnd)) > MAX_MONTHS) {
    return `Choose ${MAX_MONTHS} months or fewer.`
  }
  if (mode === 'compare') {
    if (!preStart || !preEnd) return 'Choose the "before" dates too, or switch to One period.'
    if (preStart > preEnd) return 'The "before" start date is after its end date.'
  }
  return null
}
