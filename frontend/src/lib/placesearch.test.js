import assert from 'node:assert/strict'
import test from 'node:test'

import { placeDetail, placeValue, suggestPlaces } from './placesearch.js'

const PLACES = [
  { name: 'Assam', state: 'Assam', level: 'state', aka: null },
  { name: 'Aurangabad', state: 'Bihar', level: 'district', aka: null },
  { name: 'Aurangabad', state: 'Maharashtra', level: 'district', aka: null },
  { name: 'Sibsagar', state: 'Assam', level: 'district', aka: 'sivasagar' },
  { name: 'Bangalore Urban', state: 'Karnataka', level: 'district', aka: null },
  { name: 'Karnataka', state: 'Karnataka', level: 'state', aka: null },
]

test('districts carry their state so a repeated name is never ambiguous', () => {
  assert.equal(placeValue(PLACES[0]), 'Assam')
  assert.equal(placeValue(PLACES[1]), 'Aurangabad, Bihar')
  assert.equal(placeDetail(PLACES[0]), 'State')
  assert.equal(placeDetail(PLACES[3]), 'District, Assam · also written Sivasagar')
})

test('suggestions: start of name first, states first, modern spellings found', () => {
  assert.deepEqual(suggestPlaces(PLACES, 'a'), [], 'two letters at least')
  assert.deepEqual(suggestPlaces(PLACES, 'au').map(placeValue), ['Aurangabad, Bihar', 'Aurangabad, Maharashtra'])
  assert.deepEqual(suggestPlaces(PLACES, 'siva').map((p) => p.name), ['Sibsagar'])
  assert.deepEqual(suggestPlaces(PLACES, 'urban').map((p) => p.name), ['Bangalore Urban'], 'a later word matches')
  assert.deepEqual(suggestPlaces(PLACES, 'ka').map((p) => p.name)[0], 'Karnataka')
  assert.deepEqual(suggestPlaces(PLACES, 'Aurangabad, Bihar'), [], 'nothing to suggest once picked')
  assert.deepEqual(suggestPlaces(null, 'assam'), [])
})
