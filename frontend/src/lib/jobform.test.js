import assert from 'node:assert/strict'
import test from 'node:test'

import { formFromJob } from './jobform.js'

const DEFAULTS = { analysisType: 'flood_extent', region: 'kerala', when: 'one', postStart: '2018-08-15',
  postEnd: '2018-08-25', preStart: '', preEnd: '', floodMethod: 'radar_threshold', cloudLimit: '' }

test('a flood result opened from History refills the form with its own request', () => {
  const job = { kind: 'analyze', request: { region: 'Assam', post_start: '2026-07-20', post_end: '2026-07-31',
    sensor: 'sentinel-1' }, result: { region: { name: 'Assam', admin_level: 'state' } } }
  const form = formFromJob(job, DEFAULTS)
  assert.deepEqual([form.region, form.postStart, form.postEnd, form.when], ['Assam', '2026-07-20', '2026-07-31', 'one'])
  const before = formFromJob({ ...job, request: { ...job.request, pre_start: '2026-05-01', pre_end: '2026-05-31' } }, DEFAULTS)
  assert.equal(before.when, 'compare')
  assert.equal(formFromJob({ ...job, request: { ...job.request, sensor: 'sentinel-2' } }, DEFAULTS).floodMethod, 'optical_threshold')
  assert.equal(formFromJob({ ...job, request: { ...job.request, method: 'change' } }, DEFAULTS).floodMethod, 'radar_change')
})

test('series, surface, questions, latest passes and drawn areas', () => {
  const series = formFromJob({ kind: 'series', request: { region: 'Sibsagar, Assam', start: '2026-05-01', end: '2026-09-30' } }, DEFAULTS)
  assert.deepEqual([series.when, series.region, series.postStart, series.postEnd], ['monthly', 'Sibsagar, Assam', '2026-05-01', '2026-09-30'])
  const surface = formFromJob({ kind: 'surface', request: { region: 'punjab', analysis_type: 'vegetation_health',
    post_start: '2023-06-01', post_end: '2023-10-31' } }, DEFAULTS)
  assert.equal(surface.analysisType, 'vegetation_health')
  const ask = formFromJob({ kind: 'ask', request: { question: 'How much of Assam…' }, result: {
    region: { name: 'Sibsagar', state: 'Assam', admin_level: 'district' },
    period: { post: { start: '2026-07-20', end: '2026-07-31' } } } }, DEFAULTS)
  assert.deepEqual([ask.region, ask.postStart, ask.analysisType], ['Sibsagar, Assam', '2026-07-20', 'flood_extent'])
  const latest = formFromJob({ kind: 'analyze', request: { region: 'assam', latest: true },
    result: { period: { post: { start: '2026-10-05', end: '2026-10-05' } } } }, DEFAULTS)
  assert.equal(latest.postStart, '2026-10-05')
  const drawn = formFromJob({ kind: 'analyze', request: { bbox: [1, 2, 3, 4] },
    result: { region: { name: 'user-defined rectangle', admin_level: 'custom' } } }, DEFAULTS)
  assert.equal(drawn.region, '', 'no name to put back for a drawn area')
})
