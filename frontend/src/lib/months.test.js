import assert from 'node:assert/strict'
import test from 'node:test'

import { dateProblem, firstDay, lastDay, monthOf, monthsBetween, wholeMonths } from './months.js'

test('months and their first and last days', () => {
  assert.equal(monthOf('2026-07-20'), '2026-07')
  assert.equal(monthOf(''), '')
  assert.equal(firstDay('2026-05'), '2026-05-01')
  assert.equal(lastDay('2026-09'), '2026-09-30')
  assert.equal(lastDay('2026-02'), '2026-02-28')
  assert.equal(lastDay('2024-02'), '2024-02-29')
  assert.equal(monthsBetween('2026-05', '2026-09'), 5)
  assert.equal(monthsBetween('2025-11', '2026-02'), 4)
  assert.deepEqual(wholeMonths('2026-05-15', '2026-09-03'), { postStart: '2026-05-01', postEnd: '2026-09-30' })
})

test('the date checks every mode shares', () => {
  const ok = { postStart: '2026-05-01', postEnd: '2026-09-30' }
  assert.equal(dateProblem(ok, 'monthly'), null)
  assert.match(dateProblem({ postStart: '2026-05-01', postEnd: '2018-08-31' }, 'monthly'), /first month is after/)
  assert.match(dateProblem({ postStart: '2026-07-20', postEnd: '2018-08-25' }, 'one'), /start date is after/)
  assert.match(dateProblem({ postStart: '2025-01-01', postEnd: '2026-03-31' }, 'monthly'), /12 months or fewer/)
  assert.match(dateProblem({ postStart: '', postEnd: '' }, 'monthly'), /both months/)
  assert.match(dateProblem(ok, 'compare'), /"before" dates/)
  assert.equal(dateProblem({ ...ok, preStart: '2025-05-01', preEnd: '2025-09-30' }, 'compare'), null)
})
