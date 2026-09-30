import assert from 'node:assert/strict'
import { describe as suite, it } from 'node:test'

import {
  areaKm2,
  circleAreaKm2,
  circleToRequest,
  describeCircle,
  describeFootprint,
  distanceKm,
  MAX_RADIUS_KM,
  MIN_RADIUS_KM,
  validateCircle,
  describe,
  INDIA_BOUNDS,
  isInsideIndia,
  MAX_AREA_KM2,
  normaliseBounds,
  toRequest,
  validate,
} from './bbox.js'

const ll = (lng, lat) => ({ lng, lat })

// Roughly Kuttanad, where the Kerala flood zones are.
const KUTTANAD = [76.3, 9.4, 76.6, 9.7]

suite('normaliseBounds', () => {
  it('orders a straightforward drag', () => {
    assert.deepEqual(normaliseBounds(ll(76.3, 9.4), ll(76.6, 9.7)), KUTTANAD)
  })

  it('gives the same box however the drag went', () => {
    // Dragging up-and-left is as natural as down-and-right, and a box with
    // west greater than east is rejected by the backend with a message about
    // coordinate order that tells the user nothing useful.
    const corners = [
      [ll(76.6, 9.7), ll(76.3, 9.4)],
      [ll(76.3, 9.7), ll(76.6, 9.4)],
      [ll(76.6, 9.4), ll(76.3, 9.7)],
    ]
    for (const [a, b] of corners) {
      assert.deepEqual(normaliseBounds(a, b), KUTTANAD)
    }
  })

  it('returns null when a corner is missing or broken', () => {
    assert.equal(normaliseBounds(null, ll(1, 2)), null)
    assert.equal(normaliseBounds(ll(1, 2), undefined), null)
    assert.equal(normaliseBounds(ll(NaN, 2), ll(1, 2)), null)
  })
})

suite('areaKm2', () => {
  it('scales width by latitude', () => {
    // A degree of longitude is narrower further from the equator, so the same
    // degree box is smaller in Ladakh than in Kerala. Ignoring this
    // overestimates northern areas by a third.
    const kerala = areaKm2([76, 9, 77, 10])
    const ladakh = areaKm2([77, 34, 78, 35])
    assert.ok(ladakh < kerala, 'a degree box should shrink with latitude')
  })

  it('is close to the known size of a one-degree box at the equator', () => {
    const area = areaKm2([0, 0, 1, 1])
    assert.ok(Math.abs(area - 12392) < 50, `got ${area}`)
  })

  it('is zero for a degenerate or missing box', () => {
    assert.equal(areaKm2([76, 9, 76, 9]), 0)
    assert.equal(areaKm2(null), 0)
    assert.equal(areaKm2([1, 2]), 0)
  })
})

suite('isInsideIndia', () => {
  it('accepts a box over India', () => {
    assert.equal(isInsideIndia(KUTTANAD), true)
  })

  it('rejects one that escapes the bounds', () => {
    const [, south, , north] = INDIA_BOUNDS
    assert.equal(isInsideIndia([60, south, 65, north]), false)
    assert.equal(isInsideIndia([76, 40, 77, 45]), false)
  })

  it('rejects swapped longitude and latitude', () => {
    // 9.4 E, 76.3 N is in the Norwegian Sea. Catching it here beats an Earth
    // Engine reduction over the wrong hemisphere.
    assert.equal(isInsideIndia([9.4, 76.3, 9.7, 76.6]), false)
  })
})

suite('validate', () => {
  it('passes a reasonable box', () => {
    assert.deepEqual(validate(KUTTANAD), { ok: true, reason: null })
  })

  it('refuses a box too small to measure', () => {
    const result = validate([76.3, 9.4, 76.30005, 9.40005])
    assert.equal(result.ok, false)
    assert.match(result.reason, /too small/i)
  })

  it('refuses a box beyond the area limit, and says how big it was', () => {
    const result = validate([70, 10, 90, 30])
    assert.equal(result.ok, false)
    assert.match(result.reason, /limit/i)
    assert.match(result.reason, /\d/, 'the reason should quote the actual size')
  })

  it('refuses a box outside India', () => {
    const result = validate([100, 10, 101, 11])
    assert.equal(result.ok, false)
    assert.match(result.reason, /India/)
  })

  it('always gives a reason a user can act on', () => {
    for (const bad of [null, undefined, [], [1, 2, 3]]) {
      const result = validate(bad)
      assert.equal(result.ok, false)
      assert.ok(result.reason && result.reason.length > 5)
    }
  })

  it('agrees with the backend limit rather than inventing its own', () => {
    // footprint.MAX_AREA_KM2. If these drift the user is told a box is fine
    // and then the API rejects it.
    assert.equal(MAX_AREA_KM2, 400000)
  })
})

suite('describe', () => {
  it('reads as a size and a place', () => {
    const text = describe(KUTTANAD)
    assert.match(text, /km²/)
    assert.match(text, /76\.30/)
    assert.match(text, /9\.40/)
  })

  it('is empty rather than broken when there is no box', () => {
    assert.equal(describe(null), '')
    assert.equal(describe([1, 2]), '')
  })
})

suite('toRequest', () => {
  it('sends the field name the API expects', () => {
    assert.deepEqual(Object.keys(toRequest(KUTTANAD)), ['bbox'])
  })

  it('rounds off mouse precision', () => {
    const { bbox } = toRequest([76.30000001234, 9.4, 76.6, 9.7])
    assert.equal(bbox[0], 76.3)
    for (const value of bbox) {
      assert.ok(String(value).replace(/^-?\d*\.?/, '').length <= 5)
    }
  })
})


/* --------------------------------------------------------------- circles */

suite('distanceKm', () => {
  it('measures a known distance', () => {
    // One degree of latitude is ~111 km anywhere on Earth.
    const d = distanceKm(ll(76, 9), ll(76, 10))
    assert.ok(Math.abs(d - 111.2) < 1, `got ${d}`)
  })

  it('accounts for longitude narrowing with latitude', () => {
    // The bug this replaces flat Pythagoras to avoid: at 34 N a degree of
    // longitude is ~17% shorter than at the equator, so a circle drawn in
    // Ladakh would come out a fifth too large.
    const equator = distanceKm(ll(76, 0), ll(77, 0))
    const ladakh = distanceKm(ll(76, 34), ll(77, 34))

    assert.ok(ladakh < equator)
    assert.ok(Math.abs(ladakh / equator - Math.cos((34 * Math.PI) / 180)) < 0.01)
  })

  it('is zero for a point against itself, and safe on missing input', () => {
    assert.equal(distanceKm(ll(76, 9), ll(76, 9)), 0)
    assert.equal(distanceKm(null, ll(76, 9)), 0)
    assert.equal(distanceKm(ll(76, 9), undefined), 0)
  })

  it('does not care which point came first', () => {
    const a = distanceKm(ll(76, 9), ll(77, 10))
    const b = distanceKm(ll(77, 10), ll(76, 9))
    assert.ok(Math.abs(a - b) < 1e-9)
  })
})

suite('circleAreaKm2', () => {
  it('is pi r squared', () => {
    assert.ok(Math.abs(circleAreaKm2(10) - Math.PI * 100) < 1e-9)
  })

  it('is zero for nonsense', () => {
    for (const bad of [0, -5, NaN, undefined]) {
      assert.equal(circleAreaKm2(bad), 0)
    }
  })
})

suite('validateCircle', () => {
  const centre = ll(76.44, 9.52)

  it('accepts a reasonable circle', () => {
    assert.deepEqual(validateCircle(centre, 25), { ok: true, reason: null })
  })

  it('refuses one too small to measure', () => {
    const result = validateCircle(centre, 0.1)
    assert.equal(result.ok, false)
    assert.match(result.reason, /too small/i)
  })

  it('refuses one past the radius limit, and quotes it', () => {
    const result = validateCircle(centre, 900)
    assert.equal(result.ok, false)
    assert.match(result.reason, /limit/i)
    assert.match(result.reason, /\d/)
  })

  it('agrees with the backend limits rather than inventing its own', () => {
    // footprint.MIN_RADIUS_KM and MAX_RADIUS_KM.
    assert.equal(MIN_RADIUS_KM, 0.5)
    assert.equal(MAX_RADIUS_KM, 300)
  })

  it('refuses a circle whose edge leaves India even when its centre does not', () => {
    // Near the eastern border, a large radius spills past the bound the
    // backend enforces. Checking only the centre would pass it here and fail
    // it server-side.
    const nearBorder = ll(97.5, 27)
    assert.equal(validateCircle(nearBorder, 250).ok, false)
  })

  it('always gives a reason', () => {
    for (const [c, r] of [[null, 25], [centre, NaN], [undefined, undefined]]) {
      const result = validateCircle(c, r)
      assert.equal(result.ok, false)
      assert.ok(result.reason && result.reason.length > 5)
    }
  })
})

suite('describeCircle / circleToRequest', () => {
  const centre = ll(76.44, 9.52)

  it('reads as a radius and a place', () => {
    const text = describeCircle(centre, 25)
    assert.match(text, /25\.0 km radius/)
    assert.match(text, /km²/)
  })

  it('is empty rather than broken with no circle', () => {
    assert.equal(describeCircle(null, 25), '')
    assert.equal(describeCircle(centre, NaN), '')
  })

  it('sends the field names the API expects', () => {
    const body = circleToRequest(centre, 25)
    assert.deepEqual(Object.keys(body).sort(), ['point', 'radius_km'])
    // Longitude first - the backend rejects a transposed pair, but only after
    // a round trip.
    assert.equal(body.point[0], 76.44)
    assert.equal(body.point[1], 9.52)
  })
})


suite('describeFootprint', () => {
  it('tells two drawn boxes apart', () => {
    // The whole point: "Flood extent · user-defined rectangle" is the same
    // string for every drawn analysis, so a history of them is useless.
    const assam = describeFootprint({
      name: 'user-defined rectangle',
      footprint: { kind: 'bbox', bbox: [93.44, 26.69, 93.82, 27.02],
                   approx_area_km2: 1400 },
    })
    const kerala = describeFootprint({
      name: 'user-defined rectangle',
      footprint: { kind: 'bbox', bbox: [76.3, 9.4, 76.6, 9.7],
                   approx_area_km2: 1100 },
    })

    assert.notEqual(assam, kerala)
    assert.match(assam, /26\.8[0-9]? N/)
    assert.match(kerala, /9\.5[0-9]? N/)
  })

  it('describes a circle by its radius and centre', () => {
    const text = describeFootprint({
      name: 'user-defined circle',
      footprint: { kind: 'circle', centre: [76.44, 9.52], radius_km: 25 },
    })
    assert.match(text, /25 km around/)
    assert.match(text, /9\.52 N/)
  })

  it('describes a polygon by its corner count, not its bounding box', () => {
    // A polygon response carries a bbox as well as a ring. Falling through to
    // the bbox branch would label an outline by the rectangle around it - a
    // number nothing in the analysis ever measured.
    const text = describeFootprint({
      name: 'user-defined polygon',
      footprint: {
        kind: 'polygon',
        points: 7,
        bbox: [76.0, 9.5, 77.0, 10.5],
        approx_area_km2: 12392,
      },
    })
    assert.match(text, /7-point outline/)
    assert.match(text, /10\.00 N/)
    assert.match(text, /76\.50 E/)
    assert.ok(!/12,392/.test(text), 'must not quote the bounding box area')
  })

  it('leaves named regions reading as they did', () => {
    assert.equal(describeFootprint({ name: 'Kerala' }), 'Kerala')
    assert.equal(describeFootprint({ name: 'Bangalore Urban' }), 'Bangalore Urban')
  })

  it('does not throw on a missing or malformed region', () => {
    assert.equal(describeFootprint(null), '')
    assert.equal(describeFootprint({}), '')
    assert.equal(
      describeFootprint({ name: 'x', footprint: { kind: 'bbox', bbox: [1, 2] } }),
      'x',
    )
  })
})
