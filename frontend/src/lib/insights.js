/**
 * Wording for the three things disaster staff asked for: how many people,
 * which districts, and how old the image is.
 *
 * Kept out of the components so the rules are tested: a population range is
 * never shown as a single falsely precise number, a missing figure is never
 * shown as zero, a partly observed district is never shown without saying so.
 */

const nf = (n) => Number(n).toLocaleString('en-IN')

/** "8,900 to 12,400 people", "about 450 people", or '' when unknown. */
export function peopleText(range) {
  if (!range || range.low == null) return ''
  if (range.high == null || range.low === range.high) return `about ${nf(range.low)} people`
  return `${nf(range.low)} to ${nf(range.high)} people`
}

/** Short form for table cells: "8,900–12,400". */
export function peopleCell(range) {
  if (!range || range.low == null) return '—'
  if (range.high == null || range.low === range.high) return nf(range.low)
  return `${nf(range.low)}–${nf(range.high)}`
}

/** Which models and years the figure came from, e.g. "GHSL 2020, WorldPop 2018". */
export function peopleSources(population) {
  return (population?.sources || []).map((s) => `${s.label} ${s.year}`).join(', ')
}

/**
 * The image-date line. Null when the result carries no acquisition dates.
 * Uses the age the server worked out when it returned the result.
 */
export function freshnessLine(acquisition) {
  if (!acquisition?.last) return null
  const span = acquisition.first && acquisition.first !== acquisition.last
    ? `${acquisition.first} to ${acquisition.last}`
    : acquisition.last
  const age = acquisition.age_text ? ` · newest image ${acquisition.age_text}` : ''
  return `Radar images from ${span}${age}`
}

/** True when the newest image is old enough to say so loudly (in days). */
export function isStale(acquisition, days = 7) {
  return Number.isFinite(acquisition?.age_days) && acquisition.age_days > days
}

/**
 * The next pass, estimated from the gaps between recent ones. Once that date
 * has gone by (a result opened days later, or a pass that did not happen or is
 * not in Earth Engine yet), saying "expected around 2 Oct" on 5 Oct reads as a
 * mistake - so it says the expected pass is overdue instead.
 */
export function nextPassLine(latest, today = new Date()) {
  const next = latest?.next_pass
  if (!next?.expected_on) return null
  const basis = `estimate from the last ${next.based_on_passes} passes`
  const todayIso = today.toISOString().slice(0, 10)
  if (next.expected_on < todayIso) {
    return `A pass was expected around ${next.expected_on} (${basis}); no newer image is in Earth Engine yet`
  }
  return `Next pass expected around ${next.expected_on} (${basis})`
}

export const DISTRICT_SORTS = [
  { key: 'flooded_km2', label: 'Flooded area' },
  { key: 'flooded_pct', label: '% flooded' },
  { key: 'people', label: 'People' },
  { key: 'observed_pct', label: '% seen' },
]

function districtValue(row, key) {
  if (key === 'people') return row?.people?.high ?? -1
  const v = row?.[key]
  return Number.isFinite(v) ? v : -1
}

/**
 * Districts sorted worst first by the chosen column, ties by name. Unknown
 * values sort last rather than first, so a missing people figure never puts
 * a district at the top of a "most people" list.
 */
export function sortDistricts(rows, key = 'flooded_km2') {
  if (!Array.isArray(rows)) return []
  return [...rows].sort((a, b) =>
    districtValue(b, key) - districtValue(a, key) || String(a.name).localeCompare(String(b.name)))
}

/** The caption under the table: whether the districts add up to the total. */
export function sumCheckLine(check) {
  if (!check) return ''
  if (check.within_tolerance) {
    return `Districts add up to ${check.districts_total_km2} km² of the ${check.region_total_km2} km² total.`
  }
  return `Districts add up to ${check.districts_total_km2} km² against a total of ` +
    `${check.region_total_km2} km² (${check.difference_km2 > 0 ? '+' : ''}${check.difference_km2} km²): ` +
    'the 2015 state and district boundaries do not align exactly.'
}
