import test from 'node:test'
import assert from 'node:assert/strict'

import {
  alignmentView,
  askErrorView,
  hasCoverage,
  isUpload,
  periodLine,
  sourceLine,
  validateQuestion,
  validateUpload,
  writtenBy,
} from './ask.js'

// --------------------------------------------------------------- questions

test('a real question is accepted and trimmed', () => {
  const check = validateQuestion('  Show flooding in Kerala in August 2018  ')
  assert.equal(check.ok, true)
  assert.equal(check.question, 'Show flooding in Kerala in August 2018')
})

test('empty, tiny and enormous questions are refused with a reason', () => {
  for (const text of ['', '   ', null, 'flood', 'x'.repeat(501)]) {
    const check = validateQuestion(text)
    assert.equal(check.ok, false, String(text).slice(0, 20))
    assert.ok(check.reason.length > 10)
  }
})

// ----------------------------------------------------------------- uploads

const file = (name, size) => ({ name, size })

test('the image types the backend accepts are accepted', () => {
  for (const name of ['a.png', 'b.JPG', 'c.jpeg', 'd.webp']) {
    assert.equal(validateUpload(file(name, 1000)).ok, true, name)
  }
})

test('other files are refused before the request', () => {
  assert.match(validateUpload(file('notes.txt', 100)).reason, /PNG, JPG/)
  assert.match(validateUpload(file('noextension', 100)).reason, /PNG, JPG/)
  assert.match(validateUpload(file('big.png', 21 * 1024 * 1024)).reason, /20 MB/)
  assert.match(validateUpload(file('empty.png', 0)).reason, /empty/)
  assert.match(validateUpload(null).reason, /Choose/)
})

// --------------------------------------------------------- question check

const check = (name, passed, detail = 'ok') => ({ name, passed, detail })

test('a matching question says so, quietly', () => {
  const view = alignmentView({
    passed: true, failures: [],
    checks: [check('a', true), check('b', true)],
  })
  assert.equal(view.tone, 'ok')
  assert.match(view.headline, /2 of 2/)
  assert.deepEqual(view.failures, [])
})

test('May against May is shown as a warning with its reason', () => {
  const reason = 'The baseline window is identical to the analysis window.'
  const view = alignmentView({
    passed: false, failures: [reason],
    checks: [check('windows_are_distinct', false, reason), check('b', true)],
  })
  assert.equal(view.tone, 'warn')
  assert.match(view.headline, /may not answer your question/)
  assert.deepEqual(view.failures, [reason])
})

test('failures are recovered from the checks if the list is missing', () => {
  const view = alignmentView({
    passed: false,
    checks: [check('a', false, 'The question names 08/2018 but the analysis does not cover it.')],
  })
  assert.equal(view.failures.length, 1)
})

test('no check means no block, not an empty green one', () => {
  // A result from the form, not from a question, has nothing to check.
  assert.equal(alignmentView(undefined), null)
  assert.equal(alignmentView({}), null)
})

// ------------------------------------------------------------ refusals

test('a refused question keeps its examples and what was understood', () => {
  const error = new Error('No Indian region was named in that question.')
  error.detail = {
    message: 'No Indian region was named in that question.',
    understood: 'flood extent, August 2018, no region',
    hint: "Name a state or district, for example 'in Kerala'.",
  }
  const view = askErrorView(error)
  assert.match(view.message, /No Indian region/)
  assert.match(view.understood, /no region/)
  assert.match(view.hint, /in Kerala/)
})

test('example questions come through as suggestions', () => {
  const error = new Error('Could not tell what to measure')
  error.detail = { message: 'Could not tell what to measure', try: ['Show flooding in Kendrapara'] }
  assert.deepEqual(askErrorView(error).suggestions, ['Show flooding in Kendrapara'])
})

test('a plain network error still produces a message', () => {
  const view = askErrorView(new Error('Failed to fetch'))
  assert.equal(view.message, 'Failed to fetch')
  assert.deepEqual(view.suggestions, [])
})

// ------------------------------------------------------ upload display

const UPLOAD = {
  analysis_type: 'uploaded_image_screening',
  period: null,
  observation: { sensor_used: 'uploaded RGB image', image_width_px: 1917,
                 image_height_px: 1018, scenes_used: 1, scenes_available: 1 },
  report: { generator_model: null, fallback_used: false,
            written_by: 'deterministic template (uploads are never sent to the language model)' },
}

test('an upload is never described as satellite imagery', () => {
  const line = sourceLine(UPLOAD.observation)
  assert.match(line, /Uploaded image/)
  assert.match(line, /not georeferenced/)
  assert.doesNotMatch(line, /Sentinel/)
})

test('an upload claims no coverage of any region', () => {
  // The old default printed "100.0% of the region observed" for a photo.
  assert.equal(hasCoverage(UPLOAD.observation), false)
  assert.equal(hasCoverage({ coverage_fraction: 0.9124 }), true)
})

test('an upload has no date line rather than " to "', () => {
  assert.equal(periodLine(UPLOAD.period), '')
})

test('an upload report never says "Written by null"', () => {
  const line = writtenBy(UPLOAD.report)
  assert.doesNotMatch(line, /null/)
  assert.match(line, /never sent to the language model/)
})

test('satellite results keep their existing wording', () => {
  assert.equal(
    sourceLine({ sensor_used: 'sentinel-1', scenes_used: 4, scenes_available: 4 }),
    'Sentinel-1 radar · 4/4 scenes used',
  )
  assert.match(sourceLine({ sensor_used: 'sentinel-2', scenes_used: 3, scenes_available: 6 }),
    /Sentinel-2 optical/)
  assert.equal(
    periodLine({ post: { start: '2018-08-01', end: '2018-08-31' }, pre: { start: '2018-05-01' } }),
    '2018-08-01 to 2018-08-31 · baseline 2018-05-01',
  )
  assert.equal(writtenBy({ generator_model: 'openai/gpt-oss-120b' }), 'Written by openai/gpt-oss-120b')
  assert.equal(writtenBy({ fallback_used: true }), 'Written from a template')
})

test('uploads are recognised by their analysis type', () => {
  assert.equal(isUpload(UPLOAD), true)
  assert.equal(isUpload({ analysis_type: 'water_extent' }), false)
  assert.equal(isUpload(null), false)
})
