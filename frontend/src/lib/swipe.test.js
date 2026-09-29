/**
 * Run with: npm test  (node --test, no test runner dependency)
 */

import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import {
  clampPercent,
  clipInset,
  COARSE_STEP,
  MAX_PERCENT,
  MIN_PERCENT,
  percentFromPointer,
  periodLabel,
  STEP,
  stepFromKey,
  swipeLayers,
} from './swipe.js'

const rect = { left: 100, width: 400 }

describe('clampPercent', () => {
  it('leaves a sane value alone', () => {
    assert.equal(clampPercent(42), 42)
  })

  it('holds the divider inside the map', () => {
    assert.equal(clampPercent(-30), MIN_PERCENT)
    assert.equal(clampPercent(130), MAX_PERCENT)
  })

  it('falls back to centre rather than propagating NaN', () => {
    // A NaN percent becomes "inset(0 0 0 NaN%)", which browsers ignore - the
    // clip silently stops applying and both layers draw on top of each other.
    assert.equal(clampPercent(NaN), 50)
    assert.equal(clampPercent(Infinity), MAX_PERCENT)
    assert.equal(clampPercent(undefined), 50)
  })
})

describe('percentFromPointer', () => {
  it('maps the pointer across the map width', () => {
    assert.equal(percentFromPointer(100, rect), 0)
    assert.equal(percentFromPointer(300, rect), 50)
    assert.equal(percentFromPointer(500, rect), 100)
  })

  it('is measured from the map, not the window', () => {
    // If this used clientX directly the divider would sit wrong on any page
    // where the map is not flush against the left edge.
    assert.equal(percentFromPointer(200, rect), 25)
    assert.notEqual(percentFromPointer(200, rect), 200)
  })

  it('clamps a pointer dragged outside the map', () => {
    assert.equal(percentFromPointer(0, rect), MIN_PERCENT)
    assert.equal(percentFromPointer(9999, rect), MAX_PERCENT)
  })

  it('survives a hidden map reporting zero width', () => {
    assert.equal(percentFromPointer(300, { left: 0, width: 0 }), 50)
    assert.equal(percentFromPointer(300, null), 50)
  })
})

describe('clipInset', () => {
  it('hides everything left of the divider', () => {
    assert.equal(clipInset(30), 'inset(0 0 0 30%)')
  })

  it('never emits a value the browser would discard', () => {
    for (const bad of [NaN, Infinity, -5, 250, undefined]) {
      assert.match(clipInset(bad), /^inset\(0 0 0 \d+(\.\d+)?%\)$/)
    }
  })

  it('shows the whole after layer at 0 and none of it at 100', () => {
    assert.equal(clipInset(MIN_PERCENT), 'inset(0 0 0 0%)')
    assert.equal(clipInset(MAX_PERCENT), 'inset(0 0 0 100%)')
  })
})

describe('stepFromKey', () => {
  it('moves both ways', () => {
    assert.equal(stepFromKey('ArrowRight', 50), 50 + STEP)
    assert.equal(stepFromKey('ArrowLeft', 50), 50 - STEP)
  })

  it('treats up/down like right/left, for trackpad users', () => {
    assert.equal(stepFromKey('ArrowUp', 50), stepFromKey('ArrowRight', 50))
    assert.equal(stepFromKey('ArrowDown', 50), stepFromKey('ArrowLeft', 50))
  })

  it('moves further with shift held', () => {
    assert.equal(stepFromKey('ArrowRight', 50, true), 50 + COARSE_STEP)
  })

  it('jumps to either end', () => {
    assert.equal(stepFromKey('Home', 50), MIN_PERCENT)
    assert.equal(stepFromKey('End', 50), MAX_PERCENT)
  })

  it('stops at the edges instead of wrapping', () => {
    assert.equal(stepFromKey('ArrowLeft', 0), MIN_PERCENT)
    assert.equal(stepFromKey('ArrowRight', 100), MAX_PERCENT)
  })

  it('returns null for keys it does not own', () => {
    // The caller preventDefaults only on a non-null result. Claiming Tab or
    // Enter here would trap a keyboard user on the handle.
    for (const key of ['Tab', 'Enter', 'Escape', 'a', ' ']) {
      assert.equal(stepFromKey(key, 50), null)
    }
  })
})

describe('swipeLayers', () => {
  it('pairs the baseline with the flood overlay', () => {
    const layers = swipeLayers({ flood_tiles: 'after', baseline_tiles: 'before' })
    assert.deepEqual(layers, { before: 'before', after: 'after', enabled: true })
  })

  it('stays off for a single-window analysis', () => {
    // No baseline means nothing to compare against - one layer, no divider.
    const layers = swipeLayers({ flood_tiles: 'after' })
    assert.equal(layers.enabled, false)
    assert.equal(layers.before, null)
    assert.equal(layers.after, 'after')
  })

  it('works for surface and change overlays too, not just flood', () => {
    assert.equal(swipeLayers({ surface_tiles: 's' }).after, 's')
    assert.equal(swipeLayers({ change_tiles: 'c' }).after, 'c')
  })

  it('prefers the flood overlay when several are present', () => {
    const layers = swipeLayers({ change_tiles: 'c', flood_tiles: 'f' })
    assert.equal(layers.after, 'f')
  })

  it('never enables on a baseline with nothing to compare it to', () => {
    assert.equal(swipeLayers({ baseline_tiles: 'before' }).enabled, false)
  })

  it('handles a response with no artifacts at all', () => {
    for (const input of [null, undefined, {}]) {
      assert.deepEqual(swipeLayers(input), {
        before: null,
        after: null,
        enabled: false,
      })
    }
  })
})

describe('periodLabel', () => {
  it('collapses a window inside one month', () => {
    assert.equal(periodLabel({ start: '2018-08-01', end: '2018-08-31' }), 'Aug 2018')
  })

  it('shows both ends when the window spans months', () => {
    assert.equal(
      periodLabel({ start: '2024-05-01', end: '2024-07-03' }),
      'May 2024 – Jul 2024',
    )
  })

  it('does not shift the month across a timezone', () => {
    // Parsed as UTC on purpose. Local parsing puts a 1 Aug start into July for
    // anyone west of UTC, which would mislabel the window on the map.
    assert.equal(periodLabel({ start: '2018-08-01' }), 'Aug 2018')
    assert.equal(periodLabel({ start: '2018-01-01' }), 'Jan 2018')
  })

  it('is empty when there is no period, and passes through junk', () => {
    assert.equal(periodLabel(null), '')
    assert.equal(periodLabel({}), '')
    assert.equal(periodLabel({ start: 'not-a-date' }), 'not-a-date')
  })
})
