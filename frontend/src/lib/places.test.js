import assert from 'node:assert/strict'
import { test } from 'node:test'

import { canLookUpPlaces, placesPath, placesSummary, zoneLine } from './places.js'

const result = {
  request_id: 'abc123',
  evidence: [{ quantity: 'flood_extent' }],
  zones_geojson: { features: [{ properties: { kind: 'outline' } }] },
}

test('places are looked up only for flood results with zone outlines', () => {
  assert.equal(placesPath(result), '/analyze/abc123/places')
  assert.equal(canLookUpPlaces({ ...result, zones_geojson: { features: [] } }), false)
  assert.equal(canLookUpPlaces({ ...result, evidence: [] }), false)
  assert.equal(canLookUpPlaces({ ...result, request_id: '../x' }), false)
})

test('the summary says how many zones were searched out of how many', () => {
  const block = { totals: { places: 12, road_km_by_class: { trunk: 10.25, primary: 8.1 } }, zones_searched: 25, zones_found: 92 }
  assert.equal(placesSummary(block),
    '12 villages and towns and 18.4 km of main road crossing the flood zones, in the 25 listed zones of 92.')
  assert.equal(placesSummary({ error: 'down' }), null)
})

test('a zone line stars places outside the zone and counts the rest', () => {
  const zone = {
    places: [{ name: 'A', inside: true }, { name: 'B', inside: false }, { name: 'C', inside: true }],
    roads: [{ ref: 'NH66', km: 10.98 }],
  }
  assert.deepEqual(zoneLine(zone, 2), { places: 'A, B* and 1 more', roads: 'NH66 10.98 km' })
  assert.deepEqual(zoneLine({ places: [], roads: [] }), { places: 'no named places', roads: 'no main roads' })
})
