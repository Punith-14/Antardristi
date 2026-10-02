import assert from 'node:assert/strict'
import { test } from 'node:test'

import { areaToRequestPure } from './arearequest.js'
import { boundaryArea, boundaryFileProblem, describeBoundary, pickerItems, toLatLngs } from './boundary.js'

const boundary = {
  type: 'Polygon',
  coordinates: [[[76.3, 9.4], [76.4, 9.4], [76.4, 9.5], [76.3, 9.4]]],
  source: { feature: 'Kuttanad', area_km2: 1414.2, simplified: true, area_change_pct: -0.02 },
}

test('only boundary formats the server reads are sent', () => {
  assert.equal(boundaryFileProblem({ name: 'd.geojson', size: 10 }), null)
  assert.equal(boundaryFileProblem({ name: 'D.KML', size: 10 }), null)
  assert.equal(boundaryFileProblem({ name: 'd.zip', size: 10 }), null)
  assert.match(boundaryFileProblem({ name: 'd.shp', size: 10 }), /zip them together/)
  assert.match(boundaryFileProblem({ name: 'd.pdf', size: 10 }), /GeoJSON, KML/)
  assert.match(boundaryFileProblem({ name: 'd.geojson', size: 30e6 }), /limit is 20/)
})

test('a chosen shape is described by its own name and what was done to it', () => {
  assert.equal(describeBoundary(boundaryArea(boundary)),
    'Kuttanad · uploaded boundary · 1,414 km² (simplified, area -0.02%)')
})

test('a boundary becomes exactly one area field in the request', () => {
  assert.deepEqual(areaToRequestPure(boundaryArea(boundary)), { boundary })
})

test('coordinates are flipped for Leaflet, for one polygon and for several', () => {
  assert.deepEqual(toLatLngs(boundary)[0][0], [9.4, 76.3])
  const multi = { type: 'MultiPolygon', coordinates: [boundary.coordinates, boundary.coordinates] }
  assert.equal(toLatLngs(multi).length, 2)
  assert.deepEqual(toLatLngs(null), [])
})

test('the picker shows unusable shapes with the reason, disabled', () => {
  const items = pickerItems({ features: [
    { index: 0, name: 'Alappuzha', ok: true, info: { area_km2: 1414.2 } },
    { index: 1, name: 'Bad', ok: false, error: 'crosses itself' },
  ] })
  assert.deepEqual(items.map((i) => [i.label, i.disabled]), [['Alappuzha', false], ['Bad', true]])
  assert.equal(items[1].detail, 'crosses itself')
})

test('history labels an uploaded boundary by its own name, not its bounding box', async () => {
  const { describeFootprint } = await import('./bbox.js')
  assert.equal(describeFootprint({ name: 'Kuttanad (uploaded boundary)', footprint: { kind: 'boundary', bbox: [76, 9, 77, 10] } }),
    'Kuttanad (uploaded boundary)')
})
