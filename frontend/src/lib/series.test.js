import test from 'node:test'
import assert from 'node:assert/strict'

import {
  chartGeometry,
  monthLabel,
  niceMax,
  pointTone,
  pointValue,
  prefersPercent,
  summaryLine,
} from './series.js'

const ok = (label, value, extra = {}) => ({
  label, observed: true, value, unit: 'km2', request_id: `r-${label}`,
  fraction: { value: value / 100, unit: 'percent' }, flags: [], ...extra,
})
const gap = (label) => ({
  label, observed: false, value: null, request_id: null,
  flags: [{ kind: 'unobserved', text: 'not observed' }],
})

const SERIES = [ok('2018-06', 120), gap('2018-07'), ok('2018-08', 620.3), ok('2018-09', 80)]

// ---------------------------------------------------------------- gaps

test('a gap is never drawn as a bar of height zero', () => {
  // Height zero reads as "measured, and dry". This month was not measured.
  const { bars, plotHeight } = chartGeometry(SERIES)
  const july = bars[1]
  assert.equal(july.tone, 'gap')
  assert.equal(july.value, null)
  assert.equal(july.height, plotHeight, 'a gap fills its slot as an empty outline')
})

test('a real zero is a real zero-height bar', () => {
  const { bars } = chartGeometry([ok('2018-01', 0), ok('2018-02', 10)])
  assert.equal(bars[0].tone, 'ok')
  assert.equal(bars[0].value, 0)
  assert.equal(bars[0].height, 0)
})

test('every month keeps its slot so spacing does not hide a missing one', () => {
  const { bars } = chartGeometry(SERIES, { width: 400 })
  const steps = bars.slice(1).map((b, i) => b.x - bars[i].x)
  assert.ok(steps.every((s) => Math.abs(s - steps[0]) < 1e-9))
  assert.equal(bars.length, SERIES.length)
})

// ---------------------------------------------------------------- scale

test('the axis starts at zero', () => {
  // A truncated axis makes a 5% change look like a doubling.
  const { ticks, baseline } = chartGeometry([ok('a', 600), ok('b', 630)])
  assert.equal(ticks[0].value, 0)
  assert.equal(ticks[0].y, baseline)
})

test('bar heights are proportional to values', () => {
  const { bars } = chartGeometry([ok('a', 50), ok('b', 100)])
  assert.ok(Math.abs(bars[1].height - 2 * bars[0].height) < 1e-9)
})

test('the tallest bar fits inside the plot', () => {
  const { bars, plotHeight } = chartGeometry(SERIES)
  for (const bar of bars) assert.ok(bar.height <= plotHeight + 1e-9)
})

test('axis maxima are round numbers at or above the data', () => {
  assert.equal(niceMax(620.3), 1000)
  assert.equal(niceMax(180), 200)
  assert.equal(niceMax(230), 250)
  assert.equal(niceMax(45), 50)
  assert.equal(niceMax(1), 1)
})

test('an all-dry or all-missing series still draws an axis', () => {
  assert.equal(niceMax(0), 1)
  assert.equal(niceMax(NaN), 1)
  const { bars, yMax } = chartGeometry([gap('a'), gap('b')])
  assert.equal(yMax, 1)
  assert.ok(bars.every((b) => b.tone === 'gap'))
})

test('nothing at all draws nothing rather than throwing', () => {
  for (const input of [[], null, undefined]) {
    assert.deepEqual(chartGeometry(input).bars, [])
  }
})

// ------------------------------------------------------------ percent

test('the percentage view reads the fraction record, not a rescaled km2', () => {
  const point = ok('a', 620.3, { fraction: { value: 1.79, unit: 'percent' } })
  assert.equal(pointValue(point, true), 1.79)
  assert.equal(pointValue(point, false), 620.3)
  assert.equal(chartGeometry([point], { usePercent: true }).unit, '%')
})

test('a partly observed series suggests the percentage view', () => {
  assert.equal(prefersPercent({ comparability: { partial: ['2018-09'] } }), true)
  assert.equal(prefersPercent({ comparability: { partial: [] } }), false)
})

// --------------------------------------------------------------- tones

test('tones rank the most serious flag first', () => {
  assert.equal(pointTone(gap('a')), 'gap')
  assert.equal(pointTone(ok('a', 1)), 'ok')
  assert.equal(pointTone(ok('a', 1, { flags: [{ kind: 'low_coverage' }] })), 'partial')
  assert.equal(
    pointTone(ok('a', 1, { flags: [{ kind: 'low_coverage' }, { kind: 'orbit_differs' }] })),
    'different',
  )
})

test('each bar can open its own month', () => {
  const { bars } = chartGeometry(SERIES)
  assert.equal(bars[0].requestId, 'r-2018-06')
  assert.equal(bars[1].requestId, null, 'an unobserved month has nothing to open')
})

// ------------------------------------------------------------- wording

test('month labels are short', () => {
  assert.equal(monthLabel('2018-08'), 'Aug')
  assert.equal(monthLabel('2019-01'), 'Jan')
  assert.equal(monthLabel('odd'), 'odd')
})

test('the summary says how much of the series can be compared', () => {
  const line = summaryLine({
    points: SERIES,
    comparability: { partial: ['2018-09'], differently_measured: ['2018-08'] },
  })
  assert.equal(line, '3 of 4 months observed · 1 partly · 1 measured differently')
})
