import test from 'node:test'
import assert from 'node:assert/strict'

import {
  MAX_POLYGON_POINTS,
  MIN_POLYGON_POINTS,
  actionFromKey,
  describePolygon,
  drawingHint,
  polygonAreaKm2,
  polygonBounds,
  polygonToRequest,
  selfIntersects,
  validatePolygon,
  withinSnap,
} from './polygon.js'

const pt = (lng, lat) => ({ lng, lat })

/** A roughly 1-degree square over central Kerala. */
const KERALA_SQUARE = [pt(76.0, 9.5), pt(77.0, 9.5), pt(77.0, 10.5), pt(76.0, 10.5)]

/** The same square, wound the other way. */
const REVERSED = [...KERALA_SQUARE].reverse()

// ------------------------------------------------------------------- bounds

test('bounds are west, south, east, north whatever order the points came in', () => {
  assert.deepEqual(polygonBounds(KERALA_SQUARE), [76.0, 9.5, 77.0, 10.5])
  assert.deepEqual(polygonBounds(REVERSED), [76.0, 9.5, 77.0, 10.5])
})

test('bounds of nothing is null rather than a box at the origin', () => {
  // [0,0,0,0] is a real place in the Atlantic. Null is not.
  assert.equal(polygonBounds([]), null)
  assert.equal(polygonBounds(null), null)
  assert.equal(polygonBounds([pt(76, NaN)]), null)
})

// --------------------------------------------------------------------- area

test('a one-degree square comes out near the size it should be', () => {
  // At 10 N: 111.32 km of latitude by 111.32*cos(10) = 109.63 km.
  const expected = 111.32 * 111.32 * Math.cos((10 * Math.PI) / 180)
  const actual = polygonAreaKm2(KERALA_SQUARE)
  assert.ok(
    Math.abs(actual - expected) / expected < 0.01,
    `${actual.toFixed(0)} km² vs an expected ${expected.toFixed(0)} km²`,
  )
})

test('winding direction does not change the area', () => {
  assert.ok(
    Math.abs(polygonAreaKm2(KERALA_SQUARE) - polygonAreaKm2(REVERSED)) < 0.001,
  )
})

test('longitude is narrower in the north, and the area knows it', () => {
  // The identical square of degrees, moved from Kerala to Ladakh. Flat
  // shoelace arithmetic on degrees would call these the same size; at 34 N a
  // degree of longitude is about 17% shorter than at 10 N.
  const ladakh = KERALA_SQUARE.map((p) => pt(p.lng, p.lat + 24))
  const south = polygonAreaKm2(KERALA_SQUARE)
  const north = polygonAreaKm2(ladakh)

  assert.ok(north < south, 'the northern square should measure smaller')
  const shrinkage = 1 - north / south
  assert.ok(shrinkage > 0.1 && shrinkage < 0.25, `shrank by ${(shrinkage * 100).toFixed(1)}%`)
})

test('a triangle is half its enclosing square', () => {
  const triangle = [pt(76.0, 9.5), pt(77.0, 9.5), pt(76.0, 10.5)]
  const ratio = polygonAreaKm2(triangle) / polygonAreaKm2(KERALA_SQUARE)
  assert.ok(Math.abs(ratio - 0.5) < 0.01, `ratio ${ratio.toFixed(3)}`)
})

test('fewer than three points encloses nothing', () => {
  assert.equal(polygonAreaKm2([pt(76, 9), pt(77, 9)]), 0)
  assert.equal(polygonAreaKm2([]), 0)
  assert.equal(polygonAreaKm2(null), 0)
})

test('a ring with a bad coordinate measures zero rather than NaN', () => {
  // NaN would flow into the area chip and read "NaN km²" on screen.
  const area = polygonAreaKm2([pt(76, 9.5), pt(77, undefined), pt(77, 10.5)])
  assert.equal(area, 0)
})

// -------------------------------------------------------- self-intersection

test('a simple square does not cross itself', () => {
  assert.equal(selfIntersects(KERALA_SQUARE), false)
})

test('a bowtie is caught', () => {
  // Swapping two adjacent corners folds the square into two lobes that wind
  // in opposite directions. Earth Engine would return a number for this.
  const bowtie = [pt(76.0, 9.5), pt(77.0, 9.5), pt(76.0, 10.5), pt(77.0, 10.5)]
  assert.equal(selfIntersects(bowtie), true)
})

test('a triangle can never cross itself', () => {
  assert.equal(selfIntersects([pt(76, 9.5), pt(77, 9.5), pt(76.5, 10.5)]), false)
})

test('a concave but valid outline is allowed through', () => {
  // An L-shape. Concave is not the same as self-intersecting, and refusing it
  // would rule out most real catchments and coastlines.
  const ell = [
    pt(76.0, 9.5), pt(77.0, 9.5), pt(77.0, 10.0),
    pt(76.5, 10.0), pt(76.5, 10.5), pt(76.0, 10.5),
  ]
  assert.equal(selfIntersects(ell), false)
  assert.equal(validatePolygon(ell).ok, true)
})

test('sharing the first and last vertex is how a ring closes, not a crossing', () => {
  const closed = [...KERALA_SQUARE, pt(76.0, 9.5)]
  assert.equal(selfIntersects(closed), false)
})

// --------------------------------------------------------------- validation

test('a good polygon passes', () => {
  const check = validatePolygon(KERALA_SQUARE)
  assert.equal(check.ok, true, check.reason)
  assert.equal(check.reason, null)
})

test('too few points says how many are missing', () => {
  const check = validatePolygon([pt(76, 9.5), pt(77, 9.5)])
  assert.equal(check.ok, false)
  assert.match(check.reason, /at least 3/)
  assert.match(check.reason, /have 2/)
})

test('a polygon outside India is refused', () => {
  const sahara = KERALA_SQUARE.map((p) => pt(p.lng - 60, p.lat + 10))
  const check = validatePolygon(sahara)
  assert.equal(check.ok, false)
  assert.match(check.reason, /over India/)
})

test('a huge polygon names both numbers so the refusal makes sense', () => {
  // A thin diagonal strip: small outline, enormous bounding box. Quoting only
  // the limit would be baffling to someone looking at a sliver.
  const strip = [pt(69.0, 22.0), pt(69.2, 22.0), pt(95.0, 27.0), pt(94.8, 27.0)]
  const check = validatePolygon(strip)

  assert.equal(check.ok, false)
  assert.match(check.reason, /corner to corner/)
  assert.match(check.reason, /outline/)
  assert.match(check.reason, /400,000/)
})

test('a self-intersecting shape is refused with advice, not jargon', () => {
  const bowtie = [pt(76.0, 9.5), pt(77.0, 9.5), pt(76.0, 10.5), pt(77.0, 10.5)]
  const check = validatePolygon(bowtie)

  assert.equal(check.ok, false)
  assert.match(check.reason, /crosses itself/)
  assert.match(check.reason, /Backspace|Undo|undo/)
})

test('too many points is refused at the same limit the backend keeps', () => {
  const many = Array.from({ length: MAX_POLYGON_POINTS + 1 }, (_, i) =>
    pt(76 + (i % 90) * 0.001, 9.5 + Math.floor(i / 90) * 0.001),
  )
  const check = validatePolygon(many)
  assert.equal(check.ok, false)
  assert.match(check.reason, new RegExp(String(MAX_POLYGON_POINTS)))
})

test('every refusal is a sentence a person can act on', () => {
  const bad = [
    [],
    [pt(76, 9.5), pt(77, 9.5)],
    [pt(76.0, 9.5), pt(77.0, 9.5), pt(76.0, 10.5), pt(77.0, 10.5)],
    KERALA_SQUARE.map((p) => pt(p.lng - 60, p.lat + 10)),
  ]
  for (const points of bad) {
    const { ok, reason } = validatePolygon(points)
    assert.equal(ok, false)
    assert.ok(reason.length > 25, `too terse: ${reason}`)
    assert.ok(reason[0] === reason[0].toUpperCase())
    assert.ok(/[.]$/.test(reason), `no full stop: ${reason}`)
  }
})

test('validation never throws on junk', () => {
  for (const junk of [undefined, null, 'polygon', 42, {}, [null, null, null]]) {
    const check = validatePolygon(junk)
    assert.equal(typeof check.ok, 'boolean')
    assert.ok(check.reason)
  }
})

// ------------------------------------------------------------------ request

test('the request is an open ring of [lng, lat], not [lat, lng]', () => {
  const { polygon } = polygonToRequest(KERALA_SQUARE)
  assert.equal(polygon.length, 4, 'the ring is left open for the backend to close')
  assert.deepEqual(polygon[0], [76, 9.5])
  // Longitude first. Transposed, this lands in the Indian Ocean and the
  // backend's India bounds check is the only thing that would notice.
  assert.ok(polygon.every(([lng, lat]) => lng > lat))
})

test('coordinates are rounded to something a mouse can actually mean', () => {
  const { polygon } = polygonToRequest([
    pt(76.123456789, 9.987654321), pt(77, 9.5), pt(77, 10.5),
  ])
  assert.deepEqual(polygon[0], [76.12346, 9.98765])
})

// -------------------------------------------------------------- the gesture

test('a click near the first vertex closes the ring', () => {
  assert.equal(withinSnap({ x: 100, y: 100 }, { x: 105, y: 103 }), true)
})

test('a click well away from the first vertex adds a point instead', () => {
  assert.equal(withinSnap({ x: 100, y: 100 }, { x: 140, y: 100 }), false)
})

test('snapping is measured in pixels, so zoom does not change the target size', () => {
  // Same two screen positions, whatever the map scale underneath.
  assert.equal(withinSnap({ x: 0, y: 0 }, { x: 12, y: 0 }, 12), true)
  assert.equal(withinSnap({ x: 0, y: 0 }, { x: 13, y: 0 }, 12), false)
})

test('missing points never snap', () => {
  assert.equal(withinSnap(null, { x: 1, y: 1 }), false)
  assert.equal(withinSnap({ x: 1, y: 1 }, undefined), false)
})

test('keys map to the three things a half-drawn shape can do', () => {
  assert.equal(actionFromKey('Enter'), 'finish')
  assert.equal(actionFromKey('Escape'), 'cancel')
  assert.equal(actionFromKey('Backspace'), 'undo')
  assert.equal(actionFromKey('Delete'), 'undo')
})

test('keys the tool does not own are left alone', () => {
  // Tab especially - claiming it would trap focus on the map.
  for (const key of ['Tab', 'a', 'ArrowLeft', 'Shift', ' ']) {
    assert.equal(actionFromKey(key), null)
  }
})

test('the hint changes as the ring grows', () => {
  assert.match(drawingHint([]), /first corner/)
  assert.match(drawingHint([pt(76, 9.5)]), /2 more/)
  assert.match(drawingHint([pt(76, 9.5), pt(77, 9.5)]), /1 more/)
  assert.match(drawingHint(KERALA_SQUARE), /Enter/)
})

test('once closeable, the hint says how to close', () => {
  const hint = drawingHint(KERALA_SQUARE)
  assert.match(hint, /first one/)
  assert.match(hint, /Backspace/)
  assert.match(hint, /Escape/)
})

// -------------------------------------------------------------------- label

test('the label says how many corners and how big', () => {
  const label = describePolygon(KERALA_SQUARE)
  assert.match(label, /4 points/)
  assert.match(label, /km²/)
  assert.match(label, /E/)
  assert.match(label, /N/)
})

test('an unfinished shape has no label rather than a misleading one', () => {
  assert.equal(describePolygon([pt(76, 9.5), pt(77, 9.5)]), '')
  assert.equal(describePolygon([]), '')
})

test('the label reports the outline area, not the bounding box', () => {
  // A triangle is half its box. If the label quoted the box it would read
  // twice the number Earth Engine actually reduces over.
  const triangle = [pt(76.0, 9.5), pt(77.0, 9.5), pt(76.0, 10.5)]
  const labelled = Number(describePolygon(triangle).match(/· ([\d,]+) km²/)[1].replace(/,/g, ''))
  assert.ok(
    Math.abs(labelled - polygonAreaKm2(triangle)) < 1,
    `${labelled} should be the ring area, not the box`,
  )
})

// --------------------------------------------------------------- the limits

test('the point limits match the ones the backend enforces', () => {
  assert.equal(MAX_POLYGON_POINTS, 500)
  assert.equal(MIN_POLYGON_POINTS, 3)
})
