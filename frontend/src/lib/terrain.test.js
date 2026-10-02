import assert from 'node:assert/strict'
import { test } from 'node:test'

import { requestFields } from './methods.js'
import { terrainAvailable, terrainLine } from './terrain.js'

const radar = { key: 'radar_threshold', sensor: 'sentinel-1', method: 'threshold' }
const optical = { key: 'optical_threshold', sensor: 'sentinel-2', method: 'threshold' }
const change = { key: 'radar_change', sensor: 'sentinel-1', method: 'change' }
const withRule = { flood: { terrain: { max_hand_m: 15, max_slope_deg: 20 } } }

test('the check is offered only where it was measured, and only once measured', () => {
  assert.equal(terrainAvailable(withRule, radar), true)
  assert.equal(terrainAvailable(withRule, optical), false)
  assert.equal(terrainAvailable(withRule, change), false)
  assert.equal(terrainAvailable({ flood: { terrain: null } }, radar), false)
})

test('the result line says how much was not counted and why', () => {
  const line = terrainLine({ applied: true, max_hand_m: 15, max_slope_deg: 20, excluded_km2: 312.4 })
  assert.match(line, /312\.4 km² of dark ground more than 15 m above the nearest drainage or on slopes steeper than 20°/)
  assert.match(terrainLine({ applied: false, reason: 'measured for radar only' }), /not applied: measured for radar only/)
  assert.equal(terrainLine(null), null)
})

test('switching it off is sent; leaving it on sends nothing new', () => {
  assert.deepEqual(requestFields(radar, { terrainCheck: true }), { sensor: 'sentinel-1' })
  assert.deepEqual(requestFields(radar, {}), { sensor: 'sentinel-1' })
  assert.deepEqual(requestFields(radar, { terrainCheck: false }), { sensor: 'sentinel-1', terrain_check: false })
  assert.deepEqual(requestFields(optical, { terrainCheck: false }), { sensor: 'sentinel-2' }, 'optical has no check to switch off')
})

test('with the check on, the accuracy shown is the one measured with it', async () => {
  const { effectiveOption } = await import('./terrain.js')
  const catalogue = { flood: { terrain: { text: 'more than 15 m above the nearest drainage', validation: { iou: 0.65 } } } }
  const option = { ...radar, rule: 'fused VV+VH below -18.5 dB', validation: { iou: 0.609 } }
  const on = effectiveOption(option, catalogue, {})
  assert.equal(on.validation.iou, 0.65)
  assert.match(on.rule, /not counted$/)
  assert.equal(effectiveOption(option, catalogue, { terrainCheck: false }).validation.iou, 0.609)
  assert.equal(effectiveOption(option, { flood: { terrain: null } }, {}), option)
})
