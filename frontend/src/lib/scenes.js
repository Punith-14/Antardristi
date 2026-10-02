/**
 * The satellite scenes a result came from (B5), shaped for display.
 *
 * The backend sends `scenes: {post, baseline}`, each with the scene list,
 * the total, whether the list was capped, and a line of Earth Engine code
 * that loads the same scenes. Pure functions, so `node --test` covers them.
 */

const WINDOW_LABELS = { post: 'After the event', baseline: 'Baseline (before)' }

function when(acquired) {
  if (!acquired) return '—'
  return acquired.replace('T', ' ').replace('Z', ' UTC')
}

function detail(scene, optical) {
  if (optical) {
    const cloud = scene.cloud_pct == null ? '?' : `${scene.cloud_pct}%`
    return `cloud ${cloud}${scene.tile ? ` · tile ${scene.tile}` : ''}`
  }
  const orbit = scene.relative_orbit == null ? '' : `orbit ${scene.relative_orbit}`
  return [orbit, scene.pass].filter(Boolean).join(' · ') || '—'
}

/** One entry per window that has a block, post-event first. */
export function sceneWindows(scenes) {
  if (!scenes) return []
  return ['post', 'baseline']
    .filter((key) => scenes[key])
    .map((key) => {
      const block = scenes[key]
      const window = Array.isArray(block.window) && block.window.length === 2
        ? `, ${block.window[0]} to ${block.window[1]}` : ''
      const title = `${WINDOW_LABELS[key]}${window}`
      if (block.error) return { key, title, error: block.error, rows: [], total: 0 }
      const optical = block.sensor === 'sentinel-2'
      return {
        key,
        title,
        collection: block.collection,
        total: block.total ?? 0,
        listed: block.listed ?? (block.scenes || []).length,
        truncated: Boolean(block.truncated),
        passes: block.passes || [],
        snippet: block.reproduce || null,
        rows: (block.scenes || []).map((scene) => ({
          id: scene.id,
          when: when(scene.acquired),
          platform: scene.platform || '—',
          detail: detail(scene, optical),
        })),
      }
    })
}

/** Total scenes across windows, for the section heading. */
export function sceneCount(scenes) {
  return sceneWindows(scenes).reduce((sum, w) => sum + (w.total || 0), 0)
}

/** The line under a capped list. Never lets a partial list look complete. */
export function truncationLine(window) {
  if (!window?.truncated) return null
  return `${window.listed} of ${window.total} scenes listed. The code below loads only these; ` +
    'filter the collection by area and dates for the rest.'
}

/**
 * Why there is no list. Results cached before scene lists existed have
 * none; saying so stops "no list" reading as "no scenes".
 */
export function missingScenesLine(result) {
  if (!result || result.scenes) return null
  if (!result.acquisition) return null
  return 'The scene list was not recorded for this result (computed before scene lists ' +
    'were added). Run the analysis again to see it.'
}
