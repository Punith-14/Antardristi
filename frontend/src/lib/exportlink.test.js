import test from 'node:test'
import assert from 'node:assert/strict'

import { pdfPath } from './exportlink.js'

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
