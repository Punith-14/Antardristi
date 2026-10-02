import assert from 'node:assert/strict'
import { test } from 'node:test'

import { jobError, progressLine, waitForJob } from './jobs.js'
import { can, canRun, isAdmin, loginProblem } from './session.js'

test('polls until done and reports each new step', async () => {
  const states = [
    { status: 'queued', steps: [] },
    { status: 'running', steps: [{ text: 'Finding the area' }] },
    { status: 'running', steps: [{ text: 'Finding the area' }] },
    { status: 'running', steps: [{ text: 'Finding the area' }, { text: 'Measuring flood water' }] },
    { status: 'done', steps: [{ text: 'Finding the area' }, { text: 'Measuring flood water' }], result: { request_id: 'r1' } },
  ]
  const seen = []
  let slept = 0
  const result = await waitForJob(async () => states.shift(), {
    onProgress: (steps) => seen.push(steps.length), sleep: async () => { slept += 1 },
  })
  assert.deepEqual(result, { request_id: 'r1' })
  assert.deepEqual(seen, [0, 1, 2], 'progress reported only when the steps change')
  assert.equal(slept, 4)
})

test('a failed job becomes an error carrying its detail', async () => {
  const failing = async () => ({ status: 'failed', error: { status: 404, detail: { error: 'region_not_found', message: 'No Atlantis.' } } })
  await assert.rejects(waitForJob(failing, { sleep: async () => {} }), (err) => {
    assert.equal(err.message, 'No Atlantis.')
    assert.equal(err.status, 404)
    assert.equal(err.code, 'region_not_found')
    return true
  })
  assert.equal(jobError({ detail: 'plain' }).message, 'plain')
})

test('gives up after the maximum wait, saying the job may still finish', async () => {
  let t = 0
  await assert.rejects(
    waitForJob(async () => ({ status: 'running', steps: [] }), { sleep: async () => { t += 600000 }, now: () => t }),
    /may still finish/,
  )
})

test('the progress line names the latest step', () => {
  assert.equal(progressLine([], 'queued'), 'Waiting for a free worker…')
  assert.equal(progressLine([{ text: 'A' }, { text: 'Counting people' }], 'running'), 'Step 2: Counting people…')
})

test('roles decide what the interface offers', () => {
  const viewer = { role: 'viewer' }
  const analyst = { role: 'analyst' }
  const admin = { role: 'admin' }
  assert.equal(canRun(viewer, true), false)
  assert.equal(canRun(analyst, true), true)
  assert.equal(isAdmin(analyst, true), false)
  assert.equal(isAdmin(admin, true), true)
  assert.equal(can(null, 'viewer', true), false)
  assert.equal(canRun(null, false), true, 'with sign-in off, everything is offered')
  assert.equal(loginProblem('', 'x'), 'Enter your username.')
  assert.equal(loginProblem('a', ''), 'Enter your password.')
  assert.equal(loginProblem('a', 'b'), null)
})
