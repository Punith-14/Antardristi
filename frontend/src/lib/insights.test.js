import test from 'node:test'
import assert from 'node:assert/strict'

import {
  freshnessLine,
  isStale,
  nextPassLine,
  peopleCell,
  peopleSources,
  peopleText,
  sortDistricts,
  sumCheckLine,
} from './insights.js'

// ------------------------------------------------------------------ people

test('two models are shown as a range, not one falsely precise number', () => {
  assert.equal(peopleText({ low: 8900, high: 12400 }), '8,900 to 12,400 people')
  assert.equal(peopleCell({ low: 8900, high: 12400 }), '8,900–12,400')
})

test('Indian digit grouping for large counts', () => {
  assert.equal(peopleCell({ low: 1240000, high: 1240000 }), '12,40,000')
})

test('one model, or agreeing models, give one figure marked as approximate', () => {
  assert.equal(peopleText({ low: 450, high: 450 }), 'about 450 people')
  assert.equal(peopleText({ low: 450 }), 'about 450 people')
})

test('a missing figure is shown as missing, never as zero', () => {
  assert.equal(peopleText(null), '')
  assert.equal(peopleCell(undefined), '—')
  assert.equal(peopleText({ low: 0, high: 0 }), 'about 0 people')
})

test('the sources are named with their years', () => {
  const population = { sources: [{ label: 'GHSL', year: 2020 }, { label: 'WorldPop', year: 2018 }] }
  assert.equal(peopleSources(population), 'GHSL 2020, WorldPop 2018')
})

// ------------------------------------------------------------- freshness

test('the image date line names the span and the age', () => {
  assert.equal(
    freshnessLine({ first: '2026-09-22', last: '2026-09-28', age_text: '3 days old' }),
    'Radar images from 2026-09-22 to 2026-09-28 · newest image 3 days old',
  )
  assert.equal(freshnessLine({ first: '2026-09-28', last: '2026-09-28', age_text: 'today' }),
    'Radar images from 2026-09-28 · newest image today')
})

test('no acquisition dates means no line rather than a blank one', () => {
  assert.equal(freshnessLine(null), null)
  assert.equal(freshnessLine({}), null)
})

test('an old image is flagged', () => {
  assert.equal(isStale({ age_days: 10 }), true)
  assert.equal(isStale({ age_days: 3 }), false)
  assert.equal(isStale({}), false)
})

test('the next pass is labelled an estimate', () => {
  const line = nextPassLine({ next_pass: { expected_on: '2026-10-10', based_on_passes: 4 } }, new Date('2026-10-05T10:00:00Z'))
  assert.match(line, /2026-10-10/)
  assert.match(line, /estimate/)
  assert.equal(nextPassLine({}), null)
  const overdue = nextPassLine({ next_pass: { expected_on: '2026-10-02', based_on_passes: 13 } }, new Date('2026-10-05T10:00:00Z'))
  assert.match(overdue, /was expected around 2026-10-02.*no newer image is in Earth Engine yet/)
})

// -------------------------------------------------------------- districts

const ROWS = [
  { name: 'Thrissur', flooded_km2: 60, flooded_pct: 2, observed_pct: 99, people: { high: 30000 } },
  { name: 'Alappuzha', flooded_km2: 120, flooded_pct: 8.6, observed_pct: 99, people: { high: 52000 } },
  { name: 'Wayanad', flooded_km2: 4, flooded_pct: 0.4, observed_pct: 42, people: null },
]

test('districts sort worst first by the chosen column', () => {
  assert.deepEqual(sortDistricts(ROWS).map((r) => r.name), ['Alappuzha', 'Thrissur', 'Wayanad'])
  assert.deepEqual(sortDistricts(ROWS, 'observed_pct').map((r) => r.name)[2], 'Wayanad')
})

test('a district with no people figure sorts last, not first', () => {
  assert.equal(sortDistricts(ROWS, 'people').at(-1).name, 'Wayanad')
})

test('sorting copies rather than reordering the response', () => {
  const before = ROWS.map((r) => r.name)
  sortDistricts(ROWS, 'people')
  assert.deepEqual(ROWS.map((r) => r.name), before)
  assert.deepEqual(sortDistricts(null), [])
})

test('the sum check says plainly when districts do not add up', () => {
  assert.match(sumCheckLine({ within_tolerance: true, districts_total_km2: 184, region_total_km2: 184 }),
    /add up to 184/)
  const off = sumCheckLine({ within_tolerance: false, districts_total_km2: 184,
    region_total_km2: 220, difference_km2: -36 })
  assert.match(off, /-36 km²/)
  assert.match(off, /do not align exactly/)
})
