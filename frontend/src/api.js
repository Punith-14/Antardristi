import { circleToRequest, toRequest } from './lib/bbox'

const BASE = import.meta.env.VITE_API_BASE || 'http://127.0.0.1:8000'

/** A drawn shape as the API wants it: one of bbox, or point + radius_km. */
export function areaToRequest(area) {
  if (!area) return {}
  return area.kind === 'circle'
    ? circleToRequest(area.centre, area.radiusKm)
    : toRequest(area.bbox)
}

async function request(path, options = {}) {
  const response = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })

  const body = await response.json().catch(() => ({}))

  if (!response.ok) {
    // The backend returns useful detail on 404, 409 and 422 - surface it
    // rather than a bare status code. Newer handlers send an object
    // ({error, message, matches}) instead of a string, and an unhandled object
    // renders as "[object Object]", which tells the user nothing.
    const detail = body.detail
    const message =
      typeof detail === 'string'
        ? detail
        : detail?.message || `Request failed (${response.status})`
    const error = new Error(message)
    if (detail && typeof detail === 'object') {
      error.code = detail.error
      error.matches = detail.matches
    }
    throw error
  }
  return body
}

export const api = {
  base: BASE,

  health: () => request('/'),

  /** Analyses and their measured reliability, best first. */
  catalogue: () => request('/analyses'),

  regions: () => request('/regions'),

  /** Flood extent, Sentinel-1. */
  flood: (payload) =>
    request('/analyze', { method: 'POST', body: JSON.stringify(payload) }),

  /** Vegetation, water, built-up, bare ground, moisture stress. */
  surface: (payload) =>
    request('/analyze/surface', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  zones: (requestId) => request(`/analyze/${requestId}/zones.geojson`),

  cacheStats: () => request('/cache'),
}

/** Flood lives on a different endpoint and takes different parameters. */
export function runAnalysis({
  analysisType,
  region,
  area,
  postStart,
  postEnd,
  preStart,
  preEnd,
  scale,
}) {
  const common = {
    // A drawn shape replaces the name rather than joining it. Sending both
    // makes the response harder to read than the question was - and the
    // backend would pick the shape anyway, so say so here.
    ...(area ? areaToRequest(area) : { region }),
    post_start: postStart,
    post_end: postEnd,
    pre_start: preStart || null,
    pre_end: preEnd || null,
    scale: scale || 200,
  }

  if (analysisType === 'flood_extent') {
    return api.flood({ ...common, sensor: 'sentinel-1' })
  }
  return api.surface({ ...common, analysis_type: analysisType })
}

export const RELIABILITY = {
  good: { label: 'Validated', tone: 'good' },
  moderate: { label: 'Moderate', tone: 'moderate' },
  poor: { label: 'Unreliable', tone: 'poor' },
  unvalidated: { label: 'Not validated', tone: 'unknown' },
}

/** Render [E1] citations as superscript chips linked to the evidence table. */
export function citationSegments(text) {
  if (!text) return []
  return text.split(/(\[E\d+(?:,\s*E\d+)*\])/g).map((part, index) => {
    const match = part.match(/^\[(E\d+(?:,\s*E\d+)*)\]$/)
    if (!match) return { kind: 'text', value: part, key: index }
    return {
      kind: 'citation',
      ids: match[1].split(/,\s*/),
      key: index,
    }
  })
}
