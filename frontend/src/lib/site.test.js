import assert from 'node:assert/strict'
import test from 'node:test'

import { hrefFor, parseHash, redirectFor } from './router.js'
import { gradeFromIou, reliabilityView, scoreSentence } from './reliability.js'
import {
  areaText, compactIndian, countFrame, indianNumber, peopleRange, periodText,
  quantityLabel, relativeTime, summaryCards,
} from './summary.js'
import { firstName, initials, signupProblem } from './signup.js'
import { checks, isCommon, passwordProblem, strength } from './password.js'

// ------------------------------------------------------------- router

test('hash routes map to pages and back', () => {
  assert.deepEqual(parseHash(''), { page: 'landing', section: null, params: {} })
  assert.equal(parseHash('#/accuracy').section, 'accuracy')
  assert.equal(parseHash('#/login').page, 'login')
  assert.equal(parseHash('#/app').page, 'home')
  assert.equal(parseHash('#/app/new?job=abc').params.job, 'abc')
  assert.equal(parseHash('#/app/nonsense').page, 'home')
  for (const page of ['landing', 'login', 'signup', 'home', 'new', 'ask', 'history', 'satellites', 'help', 'users']) {
    assert.equal(parseHash(hrefFor(page)).page, page)
  }
  assert.equal(hrefFor('new', { job: 'j1', empty: '' }), '#/app/new?job=j1')
})

test('signed-out visitors go to log in and come back; signed-in ones skip it', () => {
  const out = { user: null, authRequired: true }
  assert.deepEqual(redirectFor({ page: 'history', params: {} }, out), { page: 'login', params: { next: 'history' } })
  assert.equal(redirectFor({ page: 'landing', params: {} }, out), null, 'the landing page is public')
  assert.equal(redirectFor({ page: 'signup', params: {} }, out), null)
  const inn = { user: { username: 'a', role: 'analyst' }, authRequired: true }
  assert.deepEqual(redirectFor({ page: 'login', params: { next: 'ask' } }, inn), { page: 'ask', params: {} })
  assert.deepEqual(redirectFor({ page: 'users', params: {} }, inn), { page: 'home', params: {} })
  assert.equal(redirectFor({ page: 'users', params: {} }, { ...inn, isAdmin: true }), null)
  assert.equal(redirectFor({ page: 'new', params: {} }, { user: null, authRequired: false }), null)
})

// ------------------------------------------------------------- reliability

test('overlap scores grade into plain words', () => {
  assert.equal(gradeFromIou(0.609), 'good')
  assert.equal(gradeFromIou(0.466), 'moderate')
  assert.equal(gradeFromIou(0.057), 'poor')
  assert.equal(gradeFromIou(undefined), 'unvalidated')
  assert.equal(reliabilityView({ grade: 'poor', iou: 0.405 }).label, 'Rough guide only')
  assert.equal(reliabilityView({ iou: 0.888 }).label, 'Reliable')
  assert.match(scoreSentence(reliabilityView({ iou: 0.43 })), /scored 0\.43/)
  assert.match(scoreSentence(reliabilityView({})), /not been scored/)
})

// ------------------------------------------------------------- numbers

test('Indian number formats', () => {
  assert.equal(indianNumber(1234567), '12,34,567')
  assert.equal(compactIndian(210000), '2.1 lakh')
  assert.equal(compactIndian(15000000), '1.5 crore')
  assert.equal(compactIndian(950), '950')
  assert.equal(peopleRange({ low: 180000, high: 210000 }), '1.8–2.1 lakh')
  assert.equal(peopleRange({ low: 17800, high: 109100 }), '0.18–1.1 lakh')
  assert.equal(peopleRange({ low: 4000, high: 109100 }), '4,000–1.1 lakh')
  assert.equal(peopleRange({ low: 5000, high: 5000 }), '5,000')
  assert.equal(peopleRange(null), null)
  assert.equal(areaText(663.12), '663.1 km²')
  assert.equal(areaText(1.234), '1.23 km²')
})

test('dates read like a person wrote them', () => {
  assert.equal(periodText({ start: '2026-07-01', end: '2026-07-10' }), '1–10 Jul 2026')
  assert.equal(periodText({ start: '2026-06-20', end: '2026-07-10' }), '20 Jun – 10 Jul 2026')
  assert.equal(periodText({ start: '2025-12-20', end: '2026-01-10' }), '20 Dec 2025 – 10 Jan 2026')
  const now = Date.parse('2026-10-03T12:00:00Z')
  assert.equal(relativeTime('2026-10-03T11:59:30Z', now), 'just now')
  assert.equal(relativeTime('2026-10-03T11:00:00Z', now), '1 h ago')
  assert.equal(relativeTime('2026-10-02T06:00:00Z', now), 'yesterday')
  assert.equal(relativeTime('2026-09-01T06:00:00Z', now), '1 Sep 2026')
})

// ------------------------------------------------------------- summary cards

const FLOOD = {
  evidence: [
    { id: 'E1', quantity: 'flood_extent', value: 663.1, unit: 'km2' },
    { id: 'E2', quantity: 'flood_extent_fraction', value: 1.9, unit: 'percent' },
  ],
  population: { people_in_flood: { low: 17800, high: 109100 } },
  districts: { rows: [{ name: 'A', flooded_km2: 198.6 }, { name: 'B', flooded_km2: 0 }, { name: 'C', flooded_km2: 4 }] },
  observation: { scenes_used: 4, coverage_fraction: 0.91, sensor_used: 'sentinel-1' },
}

test('summary cards come straight from the result', () => {
  const cards = summaryCards(FLOOD)
  assert.deepEqual(cards.map((c) => c.key), ['area', 'people', 'districts', 'images'])
  assert.equal(cards[0].display, '663.1 km²')
  assert.equal(cards[0].hint, 'Evidence E1')
  assert.equal(cards[2].display, '2', 'districts with flood only')
  assert.equal(cards[3].display, '4')
  const surface = summaryCards({
    evidence: [{ id: 'E1', quantity: 'vegetation_area', value: 12.5, unit: 'km2' }],
    observation: { coverage_fraction: 0.5 },
  })
  assert.deepEqual(surface.map((c) => c.label), ['Healthy vegetation', 'Area seen'])
  assert.deepEqual(summaryCards({ unobserved: { reason: 'no_usable_imagery' } }), [])
  assert.equal(quantityLabel('mystery_area'), 'Mystery area')
})

test('a counting card ends exactly on its final text', () => {
  const [area, people] = summaryCards(FLOOD)
  assert.equal(countFrame(area, 0), '0 km²')
  assert.equal(countFrame(area, 1), area.display)
  assert.equal(countFrame(people, 0.3), people.display, 'text cards do not count')
})

// ------------------------------------------------------------- sign-up

test('sign-up checks name, email, type and password in that order', () => {
  const ok = { fullName: 'Asha Rao', email: 'asha@ddma.gov.in', password: 'Monsoon-River-26', userType: 'ddma' }
  assert.equal(signupProblem(ok), null)
  assert.equal(signupProblem({ ...ok, fullName: ' ' }).field, 'fullName')
  assert.equal(signupProblem({ ...ok, email: 'asha' }).field, 'email')
  assert.equal(signupProblem({ ...ok, userType: '' }).field, 'userType')
  assert.equal(signupProblem({ ...ok, password: 'short' }).field, 'password')
  assert.match(signupProblem({ ...ok, password: 'Asha-is-great-1' }).message, /must not contain/)
})

test('password rules match the server', () => {
  assert.deepEqual(checks('abc'), { length: false, upper: false, lower: true, digit: false, symbol: false })
  assert.match(passwordProblem('abcdefg1!'), /capital letter/)
  assert.match(passwordProblem('Abcdefgh1'), /symbol/)
  assert.ok(isCommon('Password@123') && isCommon('India@2026'))
  assert.match(passwordProblem('Password@123'), /too common/)
  assert.equal(passwordProblem('Monsoon-River-26'), null)
  assert.equal(strength(''), 0)
  assert.ok(strength('Monsoon-River-2026!') > strength('monsoonriver'))
})

test('names for the welcome line and avatar', () => {
  assert.equal(firstName({ full_name: 'Asha Rao' }), 'Asha')
  assert.equal(firstName({ username: 'punith' }), 'Punith')
  assert.equal(firstName({ username: 'asha.rao@x.org' }), 'Asha.rao')
  assert.equal(initials({ full_name: 'Asha Rao' }), 'AR')
  assert.equal(initials({ username: 'punith' }), 'P')
})

// ------------------------------------------------------------- history rows

import { headlineText, keepText, matchesFilter, openHref, titleOf } from './historyview.js'

test('history rows read and filter sensibly', () => {
  const flood = { job_id: 'j1', kind: 'analyze', place: 'Morigaon', analysis: 'Flood extent',
    headline: { label: 'Flooded', value: 684.2, unit: 'km2' } }
  const ask = { job_id: 'j2', kind: 'ask', question: 'Was Kerala flooded?', place: 'Kerala', analysis: 'Flood extent' }
  const veg = { job_id: 'j3', kind: 'surface', place: 'Punjab', analysis: 'Vegetation health' }
  assert.equal(headlineText(flood), '684.2 km²')
  assert.equal(headlineText(ask), null)
  assert.equal(openHref(flood), '#/app/new?job=j1')
  assert.equal(titleOf(ask), 'Was Kerala flooded?')
  assert.equal(titleOf({ kind: 'analyze' }), 'Drawn area')
  const all = [flood, ask, veg]
  assert.deepEqual(all.filter((i) => matchesFilter(i, 'flood', '')).map((i) => i.job_id), ['j1', 'j2'])
  assert.deepEqual(all.filter((i) => matchesFilter(i, 'other', '')).map((i) => i.job_id), ['j3'])
  assert.deepEqual(all.filter((i) => matchesFilter(i, 'all', 'punj')).map((i) => i.job_id), ['j3'])
})

test('saved rows keep their title; recent ones say when they go', () => {
  const now = Date.parse('2026-10-04T00:00:00Z')
  assert.equal(titleOf({ saved: true, title: 'July flood', place: 'Morigaon' }), 'July flood')
  assert.equal(titleOf({ saved: false, title: 'old', place: 'Morigaon' }), 'Morigaon')
  assert.equal(keepText({ saved: true }), 'Saved')
  assert.equal(keepText({ saved: false, expire_at: '2026-10-09T00:00:00Z' }, now), 'Removed in 5 days')
  assert.equal(keepText({ saved: false, expire_at: '2026-10-04T12:00:00Z' }, now), 'Removed tomorrow')
  const rows = [{ job_id: 'a', saved: true, note: 'DDMA meeting' }, { job_id: 'b', saved: false }]
  assert.deepEqual(rows.filter((r) => matchesFilter(r, 'saved', '')).map((r) => r.job_id), ['a'])
  assert.deepEqual(rows.filter((r) => matchesFilter(r, 'all', 'ddma')).map((r) => r.job_id), ['a'])
})
