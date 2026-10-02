import test from 'node:test'
import assert from 'node:assert/strict'

import { gisDownloads, pdfPath, planLine } from './exportlink.js'

test('a stored analysis links to its PDF', () => {
  assert.equal(
    pdfPath({ request_id: 'ae001d196fb554a36841' }),
    '/analyze/ae001d196fb554a36841/report.pdf',
  )
})

test('no request_id means no link, not a link to undefined', () => {
  for (const result of [null, undefined, {}, { request_id: null }, { request_id: '' }]) {
    assert.equal(pdfPath(result), null)
  }
})

test('an id that is not a plain token is refused', () => {
  // Placed into a URL path: a slash, query or traversal would point elsewhere.
  for (const id of ['../cache', 'a/b', 'x?y=1', 'id with space', 42, { id: 1 }]) {
    assert.equal(pdfPath({ request_id: id }), null, String(id))
  }
})

test('an answer to a question carries the question into the PDF', () => {
  assert.equal(
    pdfPath({ request_id: 'abc123', ask_id: 'q789' }),
    '/analyze/abc123/report.pdf?ask_id=q789',
  )
})

test('a malformed ask_id is dropped rather than passed into the URL', () => {
  // The PDF still downloads; it just cannot vouch for the question.
  assert.equal(
    pdfPath({ request_id: 'abc123', ask_id: 'x&request_id=other' }),
    '/analyze/abc123/report.pdf',
  )
})

test('an absurdly long id is refused', () => {
  assert.equal(pdfPath({ request_id: 'a'.repeat(65) }), null)
})

const flood = {
  request_id: 'abc123',
  evidence: [{ quantity: 'flood_extent' }],
  observation: { sensor_used: 'sentinel-1' },
  districts: { rows: [{ name: 'Alappuzha' }] },
}

test('a flood result offers zones, districts and the flood map', () => {
  const keys = gisDownloads(flood).map((d) => d.key)
  assert.deepEqual(keys, ['geojson', 'kml', 'zones_csv', 'districts_csv', 'geotiff'])
  const tif = gisDownloads(flood).find((d) => d.key === 'geotiff')
  assert.equal(tif.path, '/analyze/abc123/export/flood.zip')
  assert.equal(tif.planPath, '/analyze/abc123/export/flood-plan')
})

test('no district table, no district link; nothing observed, no flood map', () => {
  const keys = gisDownloads({ ...flood, districts: null, observation: {} }).map((d) => d.key)
  assert.deepEqual(keys, ['geojson', 'kml', 'zones_csv'])
})

test('non-flood results and unsafe ids get no GIS links', () => {
  assert.deepEqual(gisDownloads({ request_id: 'x', evidence: [{ quantity: 'water_like_pixel_fraction' }] }), [])
  assert.deepEqual(gisDownloads({ ...flood, request_id: '../etc' }), [])
  assert.deepEqual(gisDownloads(null), [])
})

test('the plan line says the delivered scale, or why it cannot be delivered', () => {
  assert.equal(planLine({ scale_m: 400, note: 'Delivered at 400 m; 200 m would exceed' }), 'Delivered at 400 m; 200 m would exceed')
  assert.match(planLine({ scale_m: null, note: 'too large' }), /too large/)
  assert.equal(planLine(null), null)
})
