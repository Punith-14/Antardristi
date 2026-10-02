import assert from 'node:assert/strict'
import { test } from 'node:test'

import { hindiPath, hindiView, withHindi } from './hindi.js'

const result = { request_id: 'abc123', report: { text: 'Flood water covered 64.0 km2 [E1].' }, verification: { passed: true } }

test('only a verified English report is offered in Hindi', () => {
  assert.equal(hindiPath(result), '/analyze/abc123/report/hi')
  assert.equal(hindiPath({ ...result, verification: { passed: false } }), null)
  assert.equal(hindiPath({ ...result, report: {} }), null)
  assert.equal(hindiPath({ ...result, request_id: '../x' }), null)
})

test('the Hindi PDF link keeps the question id', () => {
  assert.equal(withHindi('http://h/analyze/a/report.pdf'), 'http://h/analyze/a/report.pdf?lang=hi')
  assert.equal(withHindi('http://h/analyze/a/report.pdf?ask_id=q'), 'http://h/analyze/a/report.pdf?ask_id=q&lang=hi')
  assert.equal(withHindi(null), null)
})

test('a failed translation shows its reason, never its text', () => {
  assert.deepEqual(hindiView({ available: true, text: 'बाढ़' }), { text: 'बाढ़' })
  assert.deepEqual(hindiView({ available: false, reason: 'numbers changed', text: 'गलत' }), { reason: 'numbers changed' })
  assert.equal(hindiView(null), null)
})
