/**
 * Choosing the flood sensor and method (B6).
 *
 * The options, and the accuracy shown beside each, come from the backend's
 * /analyses catalogue (`flood.methods`), which reads them from the same
 * constants the detectors use. Nothing here restates a number: a second copy
 * in the frontend would drift, and the figure a user sees when CHOOSING a
 * method must be the one the result will quote.
 *
 * The rules below mirror the backend's refusals, so the form explains a
 * combination that cannot work instead of sending it and showing a 400:
 *
 *   change detection   radar only, needs a baseline window      (analysis.py)
 *   latest pass        radar threshold only                     (main.py)
 *   monthly series     one named sensor, threshold only         (main.py)
 *   cloud limit        optical only, 1-100 %                    (analysis.py)
 */

export const DEFAULT_METHOD = 'radar_threshold'

/** Used only when the catalogue could not be read: no numbers claimed. */
const FALLBACK = [{
  key: DEFAULT_METHOD,
  sensor: 'sentinel-1',
  method: 'threshold',
  label: 'Radar (Sentinel-1), standard threshold',
  validation: null,
  needs_baseline: false,
  supports_latest: true,
  default: true,
}]

export function methodOptions(catalogue) {
  const methods = catalogue?.flood?.methods
  return Array.isArray(methods) && methods.length ? methods : FALLBACK
}

export function findOption(options, key) {
  return options.find((o) => o.key === key) || options.find((o) => o.default) || options[0]
}

/** "IoU 0.609 · precision 0.807 · recall 0.713, measured at 200 m" */
export function accuracyLine(option) {
  const v = option?.validation
  if (!v || v.iou == null) return 'No accuracy figure: not yet measured.'
  const at = option.measured_at_m ? `, measured at ${option.measured_at_m} m` : ''
  return `IoU ${v.iou} · precision ${v.precision} · recall ${v.recall}${at}`
}

/**
 * Whether an option can run in a mode ('single', 'latest' or 'series') with
 * the form as it stands. {ok, reason}; the reason is shown, never hidden.
 */
export function availability(option, form, mode = 'single') {
  if (!option) return { ok: false, reason: 'Choose a method.' }
  if (mode === 'latest' && !option.supports_latest) {
    return {
      ok: false,
      reason: 'Latest-pass mode follows Sentinel-1 passes and uses the standard radar threshold.',
    }
  }
  if (mode === 'series' && option.method !== 'threshold') {
    return {
      ok: false,
      reason: 'A monthly series uses the standard threshold, so every month is measured the same way.',
    }
  }
  if (mode === 'single' && option.needs_baseline && !(form?.preStart && form?.preEnd)) {
    return {
      ok: false,
      reason: 'Change detection compares against a dry baseline: set one under "Compare against an earlier period".',
    }
  }
  if (option.sensor === 'sentinel-2' && form?.cloudLimit !== '' && form?.cloudLimit != null) {
    const limit = Number(form.cloudLimit)
    if (!Number.isInteger(limit) || limit < 1 || limit > 100) {
      return { ok: false, reason: 'Cloud limit must be a whole number from 1 to 100.' }
    }
  }
  return { ok: true, reason: null }
}

/**
 * The request fields for a choice. Defaults are left out (method
 * "threshold", cloud limit unset), so a default request keeps the cache key,
 * and the request_id, it always had.
 */
export function requestFields(option, form) {
  const fields = { sensor: option?.sensor || 'sentinel-1' }
  if (option?.method && option.method !== 'threshold') fields.method = option.method
  if (option?.sensor === 'sentinel-2' && form?.cloudLimit !== '' && form?.cloudLimit != null) {
    fields.cloud_limit = Number(form.cloudLimit)
  }
  // The terrain check is on by default where it exists; only switching it
  // off is sent, so a default request keeps its cache key.
  if (form?.terrainCheck === false && option?.sensor === 'sentinel-1' && option?.method === 'threshold') {
    fields.terrain_check = false
  }
  return fields
}

/** A short line on what the choice cannot do, shown under the selector. */
export function limitsLine(option) {
  return option?.limits || null
}
