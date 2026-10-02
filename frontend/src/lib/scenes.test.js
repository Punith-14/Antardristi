import assert from 'node:assert/strict'
import { test } from 'node:test'

import { missingScenesLine, sceneCount, sceneWindows, truncationLine } from './scenes.js'

const post = {
  sensor: 'sentinel-1',
  collection: 'COPERNICUS/S1_GRD',
  window: ['2018-08-01', '2018-08-31'],
  total: 3,
  listed: 3,
  truncated: false,
  passes: ['descending'],
  reproduce: 'import ee\n...',
  scenes: [
    { id: 'S1A_A', acquired: '2018-08-01T00:52:13Z', platform: 'Sentinel-1A', relative_orbit: 63, pass: 'descending' },
  ],
}

test('windows come post first, then baseline, with their dates', () => {
  const baseline = { ...post, window: ['2018-05-01', '2018-05-31'], total: 2 }
  const windows = sceneWindows({ baseline, post })
  assert.deepEqual(windows.map((w) => w.key), ['post', 'baseline'])
  assert.equal(windows[0].title, 'After the event, 2018-08-01 to 2018-08-31')
  assert.equal(windows[1].title, 'Baseline (before), 2018-05-01 to 2018-05-31')
})

test('radar rows show time, satellite, orbit and pass', () => {
  const [row] = sceneWindows({ post })[0].rows
  assert.deepEqual(row, { id: 'S1A_A', when: '2018-08-01 00:52:13 UTC', platform: 'Sentinel-1A', detail: 'orbit 63 · descending' })
})

test('optical rows show cloud and tile instead', () => {
  const optical = { ...post, sensor: 'sentinel-2', scenes: [{ id: 'X', acquired: '2018-08-15T05:10:00Z', platform: 'Sentinel-2A', cloud_pct: 12.3, tile: '43PFL' }] }
  assert.equal(sceneWindows({ post: optical })[0].rows[0].detail, 'cloud 12.3% · tile 43PFL')
})

test('a missing baseline is skipped, and the count sums the windows', () => {
  assert.equal(sceneWindows({ post, baseline: null }).length, 1)
  assert.equal(sceneCount({ post, baseline: { ...post, total: 2 } }), 5)
  assert.equal(sceneCount(null), 0)
})

test('a window whose list failed says so', () => {
  const [w] = sceneWindows({ post: { window: ['a', 'b'], error: 'scene list could not be read: quota' } })
  assert.match(w.error, /quota/)
  assert.deepEqual(w.rows, [])
})

test('a capped list says it is capped, and an uncapped one says nothing', () => {
  assert.equal(truncationLine(sceneWindows({ post })[0]), null)
  const capped = sceneWindows({ post: { ...post, total: 70, listed: 50, truncated: true } })[0]
  assert.match(truncationLine(capped), /^50 of 70 scenes listed/)
})

test('an older result without a list explains why', () => {
  assert.match(missingScenesLine({ acquisition: { last: '2018-08-01' } }), /not recorded/)
  assert.equal(missingScenesLine({ acquisition: { last: 'x' }, scenes: { post } }), null)
  assert.equal(missingScenesLine({}), null, 'not a flood result: say nothing')
})
