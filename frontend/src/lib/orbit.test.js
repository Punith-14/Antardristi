import assert from 'node:assert/strict'
import test from 'node:test'

import {
  areaCentre, countdown, direction, distanceKm, distanceToArea, istTime, latLonText,
  nextPass, placeLabel, satrecFor, stateAt, track,
} from './orbit.js'

// Elements in CelesTrak's OMM (JSON) form for a Sentinel-1-like orbit:
// sun-synchronous, 98.18 degrees, 14.59 revolutions a day (about 693 km).
const SAT = {
  name: 'Sentinel-1C',
  omm: {
    OBJECT_NAME: 'SENTINEL-1C', OBJECT_ID: '2024-235A', EPOCH: '2026-10-03T06:00:00.000',
    MEAN_MOTION: 14.59198, ECCENTRICITY: 0.0001, INCLINATION: 98.18, RA_OF_ASC_NODE: 280.1,
    ARG_OF_PERICENTER: 80.2, MEAN_ANOMALY: 279.9, EPHEMERIS_TYPE: 0, CLASSIFICATION_TYPE: 'U',
    NORAD_CAT_ID: 62261, ELEMENT_SET_NO: 999, REV_AT_EPOCH: 9700, BSTAR: 0.00002,
    MEAN_MOTION_DOT: 0.0000012, MEAN_MOTION_DDOT: 0,
  },
}
const AT = new Date('2026-10-03T08:00:00Z')

const INDIA = { type: 'Polygon', coordinates: [[[68.2, 23.6], [72.7, 19.0], [77.5, 8.1], [80.3, 13.1], [88.9, 21.6],
  [97.4, 28.3], [80.1, 28.8], [74.6, 37.0], [70.1, 27.9], [68.2, 23.6]]] }

test('SGP4 puts a Sentinel-1 orbit where it should be', () => {
  const satrec = satrecFor(SAT)
  const st = stateAt(satrec, AT)
  assert.ok(st.altKm > 680 && st.altKm < 720, `altitude ${st.altKm}`)
  assert.ok(st.speedKmS > 7.3 && st.speedKmS < 7.7, `speed ${st.speedKmS}`)
  assert.ok(Math.abs(st.lat) <= 90 && Math.abs(st.lon) <= 180)
  assert.ok(['Ascending', 'Descending'].includes(direction(satrec, AT)))
  const points = track(satrec, AT, { minutesBack: 49, minutesAhead: 49, stepS: 60 })
  assert.equal(points.length, 99)
  assert.ok(Math.max(...points.map((p) => p.lat)) > 80, 'a polar orbit reaches high latitudes')
  assert.equal(stateAt(null, AT), null)
})

test('distances to points and areas', () => {
  assert.ok(Math.abs(distanceKm({ lat: 28.61, lon: 77.21 }, { lat: 19.08, lon: 72.88 }) - 1150) < 15, 'Delhi to Mumbai')
  assert.equal(distanceToArea({ lat: 22, lon: 79 }, INDIA), 0, 'inside')
  const offshore = distanceToArea({ lat: 15, lon: 66 }, INDIA)
  assert.ok(offshore > 400 && offshore < 900, `Arabian Sea point ${offshore}`)
  const c = areaCentre(INDIA)
  assert.ok(Math.abs(c.lon - 82.8) < 1e-9 && Math.abs(c.lat - 22.55) < 1e-9)
})

test('a polar orbit passes near India within a day, and the pass is a window', () => {
  const satrec = satrecFor(SAT)
  const pass = nextPass(satrec, INDIA, AT, { hours: 24, stepS: 30 })
  assert.ok(pass, 'found a pass')
  assert.ok(pass.end > pass.start && pass.start >= AT)
  assert.ok(pass.end - pass.start < 30 * 60000, 'a pass lasts minutes, not hours')
  assert.ok(pass.closestKm <= 300)
  assert.equal(nextPass(satrec, { type: 'Point', coordinates: [0, 0] }, AT), null, 'no area, no pass')
})

test('times read like a person wrote them', () => {
  assert.equal(countdown(30000), 'now')
  assert.equal(countdown(4 * 60000), 'in 4 min')
  assert.equal(countdown((2 * 60 + 10) * 60000), 'in 2 h 10 min')
  assert.equal(countdown((27 * 60) * 60000), 'in 1 d 3 h')
  assert.equal(countdown(-6 * 3600000), '6 h 0 min ago')
  assert.equal(istTime('2026-10-03T00:42:00Z'), '3 Oct, 06:12 IST')
  assert.equal(latLonText(14.21, -86.94), '14.2° N, 86.9° W')
})

test('places are named, seas around India by name', () => {
  const india = [{ properties: { name: 'India' }, geometry: INDIA }]
  assert.equal(placeLabel(22, 79, india), 'India')
  assert.equal(placeLabel(15, 88, india), 'the Bay of Bengal')
  assert.equal(placeLabel(15, 65, india), 'the Arabian Sea')
  assert.equal(placeLabel(-30, 80, india), 'the Indian Ocean')
  assert.equal(placeLabel(10, -150, india), 'the Pacific Ocean')
  assert.equal(placeLabel(20, -40, india), 'the Atlantic Ocean')
})
