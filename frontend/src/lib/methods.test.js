import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  DEFAULT_METHOD, accuracyLine, availability, findOption, methodOptions, requestFields,
} from './methods.js'

// Shaped like /analyses flood.methods. The numbers here are test data only;
// the real ones come from the backend's constants.
const catalogue = {
  flood: {
    methods: [
      { key: 'radar_threshold', sensor: 'sentinel-1', method: 'threshold', validation: { iou: 0.6, precision: 0.8, recall: 0.7 }, measured_at_m: 200, needs_baseline: false, supports_latest: true, default: true },
      { key: 'optical_threshold', sensor: 'sentinel-2', method: 'threshold', validation: { iou: 0.7, precision: 0.8, recall: 0.9 }, measured_at_m: 10, caveat: 'flatters optical', needs_baseline: false, supports_latest: false, default_cloud_limit: 40 },
      { key: 'radar_change', sensor: 'sentinel-1', method: 'change', validation: { iou: 0.5, precision: 0.7, recall: 0.6 }, measured_at_m: 10, needs_baseline: true, supports_latest: false },
    ],
  },
}
const options = methodOptions(catalogue)
const radar = findOption(options, 'radar_threshold')
const opticalOpt = findOption(options, 'optical_threshold')
const change = findOption(options, 'radar_change')
const noBaseline = { preStart: '', preEnd: '', cloudLimit: '' }
const withBaseline = { preStart: '2018-05-01', preEnd: '2018-05-31', cloudLimit: '' }

test('options come from the catalogue; without it, only the radar default and no numbers', () => {
  assert.equal(options.length, 3)
  const fallback = methodOptions(null)
  assert.deepEqual(fallback.map((o) => o.key), [DEFAULT_METHOD])
  assert.equal(fallback[0].validation, null)
  assert.match(accuracyLine(fallback[0]), /not yet measured/)
})

test('an unknown key falls back to the default option', () => {
  assert.equal(findOption(options, 'nonsense').key, 'radar_threshold')
})

test('each option shows its own measured accuracy and scale', () => {
  assert.equal(accuracyLine(radar), 'IoU 0.6 · precision 0.8 · recall 0.7, measured at 200 m')
  assert.match(accuracyLine(change), /IoU 0.5 .* measured at 10 m/)
})

test('change detection needs a baseline window', () => {
  assert.equal(availability(change, noBaseline).ok, false)
  assert.match(availability(change, noBaseline).reason, /baseline/)
  assert.equal(availability(change, withBaseline).ok, true)
})

test('latest-pass mode is radar threshold only', () => {
  assert.equal(availability(radar, noBaseline, 'latest').ok, true)
  assert.equal(availability(opticalOpt, noBaseline, 'latest').ok, false)
  assert.equal(availability(change, withBaseline, 'latest').ok, false)
})

test('a monthly series takes either sensor but only the threshold method', () => {
  assert.equal(availability(radar, noBaseline, 'series').ok, true)
  assert.equal(availability(opticalOpt, noBaseline, 'series').ok, true)
  assert.equal(availability(change, withBaseline, 'series').ok, false)
})

test('the cloud limit must be a whole percentage', () => {
  for (const bad of ['0', '101', '12.5', 'abc']) {
    assert.equal(availability(opticalOpt, { ...noBaseline, cloudLimit: bad }).ok, false, bad)
  }
  assert.equal(availability(opticalOpt, { ...noBaseline, cloudLimit: '60' }).ok, true)
  assert.equal(availability(opticalOpt, noBaseline).ok, true, 'blank means the default')
})

test('the default choice sends what the form always sent, so cache keys do not move', () => {
  assert.deepEqual(requestFields(radar, noBaseline), { sensor: 'sentinel-1' })
})

test('each choice produces the request the backend expects', () => {
  assert.deepEqual(requestFields(change, withBaseline), { sensor: 'sentinel-1', method: 'change' })
  assert.deepEqual(requestFields(opticalOpt, noBaseline), { sensor: 'sentinel-2' })
  assert.deepEqual(requestFields(opticalOpt, { cloudLimit: '60' }), { sensor: 'sentinel-2', cloud_limit: 60 })
  assert.deepEqual(requestFields(radar, { cloudLimit: '60' }), { sensor: 'sentinel-1' }, 'cloud limit is optical only')
})
