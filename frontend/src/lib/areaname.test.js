import { test } from 'node:test'
import assert from 'node:assert/strict'
import { areaCentre, areaTitle } from './areaname.js'

test('centre of a box, a circle and a polygon', () => {
  assert.deepEqual(areaCentre({ kind: 'bbox', bbox: [94, 27, 96, 29] }), { lat: 28, lng: 95 })
  assert.deepEqual(areaCentre({ kind: 'circle', centre: { lat: 27.4, lng: 94.9 }, radiusKm: 10 }), { lat: 27.4, lng: 94.9 })
  assert.deepEqual(areaCentre({ kind: 'polygon', points: [{ lat: 10, lng: 76 }, { lat: 12, lng: 76 }, { lat: 11, lng: 78 }] }), { lat: 11, lng: 77 })
  assert.equal(areaCentre({ kind: 'boundary' }), null)
  assert.equal(areaCentre(null), null)
})

test('title uses the place when known', () => {
  assert.equal(areaTitle({ kind: 'circle' }, 'Dibrugarh, Assam'), 'Circle near Dibrugarh, Assam')
  assert.equal(areaTitle({ kind: 'bbox' }, null), 'Box drawn on the map')
  assert.equal(areaTitle({ kind: 'polygon' }, 'Alappuzha, Kerala'), 'Drawn area near Alappuzha, Kerala')
})
