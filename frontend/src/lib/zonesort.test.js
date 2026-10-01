import test from 'node:test'
import assert from 'node:assert/strict'

import {
  SEVERITY_ORDER,
  SORTS,
  sortZones,
  defaultDirection,
  nextSort,
} from './zonesort.js'

function zone(rank, area, severity, north = 10) {
  return {
    id: `z${rank}`,
    rank,
    area_km2: area,
    severity,
    centroid: [76.0, north],
  }
}

// Deliberately not already in any sorted order.
const ZONES = [
  zone(1, 120.5, 'high', 11.2),
  zone(2, 80.0, 'low', 9.4),
  zone(3, 45.25, 'moderate', 12.8),
  zone(4, 45.25, 'high', 8.1),
]

function ranks(list) {
  return list.map((z) => z.rank)
}

// ------------------------------------------------------------------ ordering

test('rank ascending is the default and matches the backend order', () => {
  assert.deepEqual(ranks(sortZones(ZONES)), [1, 2, 3, 4])
})

test('area descending puts the largest zone first', () => {
  assert.deepEqual(ranks(sortZones(ZONES, 'area', 'desc')), [1, 2, 3, 4])
})

test('area ascending puts the smallest zone first', () => {
  const sorted = sortZones(ZONES, 'area', 'asc')
  assert.equal(sorted[0].area_km2, 45.25)
  assert.equal(sorted[sorted.length - 1].area_km2, 120.5)
})

test('severity sorts worst first, not alphabetically', () => {
  // Alphabetical would be high, low, moderate - putting "low" second.
  const sorted = sortZones(ZONES, 'severity')
  assert.deepEqual(sorted.map((z) => z.severity), ['high', 'high', 'moderate', 'low'])
})

test('severity descending sorts least severe first', () => {
  const sorted = sortZones(ZONES, 'severity', 'desc')
  assert.equal(sorted[0].severity, 'low')
})

test('latitude sorts south to north when ascending', () => {
  const sorted = sortZones(ZONES, 'north', 'asc')
  const lats = sorted.map((z) => z.centroid[1])
  assert.deepEqual(lats, [...lats].sort((a, b) => a - b))
})

test('ties break by rank so the order never wobbles between renders', () => {
  // Zones 3 and 4 have identical areas.
  const first = ranks(sortZones(ZONES, 'area', 'desc'))
  const second = ranks(sortZones([...ZONES].reverse(), 'area', 'desc'))
  assert.deepEqual(first, second)
  assert.deepEqual(first.slice(2), [3, 4])
})

test('sorting returns a copy and leaves the response array untouched', () => {
  const original = ranks(ZONES)
  const sorted = sortZones(ZONES, 'area', 'asc')
  assert.notEqual(sorted, ZONES)
  assert.deepEqual(ranks(ZONES), original)
})

test('every zone survives the sort', () => {
  for (const { key } of SORTS) {
    for (const direction of ['asc', 'desc']) {
      assert.equal(sortZones(ZONES, key, direction).length, ZONES.length)
    }
  }
})

// -------------------------------------------------------------- bad input

test('a missing or malformed zone list sorts to empty rather than throwing', () => {
  for (const input of [undefined, null, 'zones', 42, {}]) {
    assert.deepEqual(sortZones(input), [])
  }
})

test('a zone missing the field being sorted on does not break the order', () => {
  const ragged = [...ZONES, { id: 'zx', rank: 5 }]
  for (const { key } of SORTS) {
    assert.equal(sortZones(ragged, key).length, 5)
  }
})

test('an unknown severity sorts last instead of first', () => {
  const odd = [zone(1, 10, 'catastrophic'), zone(2, 10, 'high')]
  assert.equal(sortZones(odd, 'severity')[0].severity, 'high')
})

test('an unknown sort key falls back to rank', () => {
  assert.deepEqual(ranks(sortZones(ZONES, 'colour')), [1, 2, 3, 4])
})

// ----------------------------------------------------------- the interaction

test('the first click on area shows the biggest zones', () => {
  assert.equal(defaultDirection('area'), 'desc')
})

test('rank and severity start ascending because rank 1 is already the worst', () => {
  assert.equal(defaultDirection('rank'), 'asc')
  assert.equal(defaultDirection('severity'), 'asc')
})

test('clicking a different column switches to it in its natural direction', () => {
  assert.deepEqual(
    nextSort({ key: 'rank', direction: 'asc' }, 'area'),
    { key: 'area', direction: 'desc' },
  )
})

test('clicking the active column flips the direction', () => {
  const first = nextSort({ key: 'area', direction: 'desc' }, 'area')
  assert.deepEqual(first, { key: 'area', direction: 'asc' })
  assert.deepEqual(nextSort(first, 'area'), { key: 'area', direction: 'desc' })
})

// ------------------------------------------------------------------- shape

test('every offered sort key is one the comparator actually handles', () => {
  // A label in the UI with no matching case silently sorts by rank while
  // claiming to sort by something else.
  const handled = new Set(['rank', 'area', 'severity', 'north', 'people'])
  for (const { key, label } of SORTS) {
    assert.ok(handled.has(key), `no comparator for ${key}`)
    assert.ok(label && label.length > 1)
  }
})

test('severity order covers exactly the severities the backend emits', () => {
  assert.deepEqual(Object.keys(SEVERITY_ORDER).sort(), ['high', 'low', 'moderate'])
})


test('people sorts most first, and an unknown figure sorts below zero', () => {
  const zones = [
    { id: 'a', rank: 1, population: { low: 100, high: 200 } },
    { id: 'b', rank: 2 },
    { id: 'c', rank: 3, population: { low: 0, high: 0 } },
    { id: 'd', rank: 4, population: { low: 900, high: 1500 } },
  ]
  const sorted = sortZones(zones, 'people', defaultDirection('people'))
  assert.deepEqual(sorted.map((z) => z.id), ['d', 'a', 'c', 'b'])
})
