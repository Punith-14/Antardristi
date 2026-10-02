import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'

import { accuracyRows } from './accuracy.js'
import { GLOSSARY, LIMITS, QUANTITY_TERMS, STEPS, hindiTerms, searchGlossary, tooltip } from './glossary.js'

const here = dirname(fileURLToPath(import.meta.url))
const src = join(here, '..')
const read = (...p) => readFileSync(join(...p), 'utf8')

test('every <Term k="..."> used in a component has a glossary entry', () => {
  const used = new Set()
  for (const file of readdirSync(join(src, 'components'))) {
    for (const m of read(src, 'components', file).matchAll(/<Term k="([a-z_0-9]+)"/g)) used.add(m[1])
  }
  assert.ok(used.size >= 5, `found only ${used.size} terms in use`)
  for (const key of used) assert.ok(GLOSSARY[key], `no glossary entry for "${key}"`)
})

test('every evidence quantity the flood path emits has a term', () => {
  const analysis = read(src, '..', '..', 'backend', 'pipeline', 'analysis.py')
  const quantities = [...analysis.matchAll(/builder\.add\(\s*"([a-z_]+)"/g)].map((m) => m[1])
  assert.ok(quantities.length >= 8, 'did not find the quantities')
  quantities.push('population_in_flood_extent_ghsl', 'population_in_flood_extent_worldpop')
  for (const q of quantities) {
    assert.ok(QUANTITY_TERMS[q], `evidence quantity "${q}" has no glossary term`)
    assert.ok(GLOSSARY[QUANTITY_TERMS[q]], `"${q}" maps to a missing term`)
  }
})

test('no accuracy figure is written into the glossary or the panel', () => {
  for (const file of [join(here, 'glossary.js'), join(here, 'accuracy.js'), join(src, 'components', 'HowItWorks.jsx')]) {
    const figures = readFileSync(file, 'utf8').match(/\b0\.\d{2,}\b/g)
    assert.equal(figures, null, `${file} hard-codes ${figures}`)
  }
})

test('every term has a definition, a reason and Hindi in Devanagari', () => {
  for (const [key, e] of Object.entries(GLOSSARY)) {
    assert.ok(e.term && e.short && e.why, key)
    assert.match(e.hindi, /[ऀ-ॿ]/, `${key} Hindi is not Devanagari`)
  }
  assert.equal(Object.keys(hindiTerms()).length, Object.keys(GLOSSARY).length)
  assert.ok(LIMITS.length >= 6 && LIMITS.every((l) => l.source))
  assert.equal(STEPS.length, 5)
})

test('search finds terms in English and in Hindi', () => {
  assert.deepEqual(searchGlossary('iou').map((t) => t.key), ['iou'])
  assert.ok(searchGlossary('बाढ़').some((t) => t.key === 'flood_extent'))
  assert.equal(searchGlossary('').length, Object.keys(GLOSSARY).length)
  assert.match(tooltip('recall'), /missed flooding/)
  assert.equal(tooltip('nope'), '')
})

test('the accuracy table is built from the catalogue alone', () => {
  assert.deepEqual(accuracyRows(null), [])
  const rows = accuracyRows({
    flood: {
      methods: [{ key: 'radar_threshold', label: 'Radar', validation: { iou: 0.6, precision: 0.8, recall: 0.7 }, measured_at_m: 200 }],
      validation: { india: { event: 'Assam, August 2016', chips: 28, iou: 0.7, precision: 0.9, recall: 0.8, ci95: [0.5, 0.8] } },
    },
    surface: [{ type: 'water_extent', label: 'Surface water', iou: null }],
  })
  assert.deepEqual(rows.map((r) => r.key), ['radar_threshold', 'india', 'surface_water_extent'])
  assert.equal(rows[0].scale, '200 m')
  assert.match(rows[1].note, /95% range 0.5 to 0.8/)
  assert.equal(rows[2].note, 'not validated')
})
