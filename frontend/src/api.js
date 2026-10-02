import { circleToRequest, toRequest } from './lib/bbox'
import { polygonToRequest } from './lib/polygon'
import { gisDownloads, pdfPath } from './lib/exportlink'

const BASE = import.meta.env.VITE_API_BASE || 'http://127.0.0.1:8000'

/**
 * A drawn shape as the API wants it: one of bbox, point + radius_km, or
 * polygon.
 *
 * Exactly one key comes back. footprint.build refuses a request carrying two
 * shapes rather than picking one, because a caller that sent both could not
 * tell which was measured - so this must never merge them.
 */
export function areaToRequest(area) {
  if (!area) return {}
  if (area.kind === 'circle') return circleToRequest(area.centre, area.radiusKm)
  if (area.kind === 'polygon') return polygonToRequest(area.points)
  return toRequest(area.bbox)
}

/** Where the PDF of a stored analysis can be downloaded, or null. */
export function pdfUrl(result) {
  const path = pdfPath(result)
  return path ? `${BASE}${path}` : null
}

/** GIS downloads for a result, with full URLs. See lib/exportlink. */
export function gisLinks(result) {
  return gisDownloads(result).map((item) => ({
    ...item,
    url: `${BASE}${item.path}`,
  }))
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
      // The whole object, not just the fields picked above. /ask refuses with
      // what it understood and example questions; dropping those told a user
      // what was wrong and nothing about how to fix it.
      error.detail = detail
    }
    throw error
  }
  return body
}

/**
 * A multipart upload. Separate from request() because that sets a JSON
 * Content-Type, and a file upload needs the browser to write its own
 * multipart boundary into that header.
 */
async function upload(path, formData) {
  const response = await fetch(`${BASE}${path}`, { method: 'POST', body: formData })
  const body = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = body.detail
    const error = new Error(
      typeof detail === 'string' ? detail : detail?.message || `Upload failed (${response.status})`,
    )
    if (detail && typeof detail === 'object') error.detail = detail
    throw error
  }
  return body
}

/** A file the backend serves (an upload overlay, say) as a full URL. */
export function assetUrl(path) {
  if (!path || typeof path !== 'string' || !path.startsWith('/outputs/')) return null
  return `${BASE}${path}`
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

  /**
   * A plain-language question. A drawn area, if any, is sent with it: the
   * question still decides what to measure and when, the shape decides where.
   */
  ask: ({ question, area }) =>
    request('/ask', {
      method: 'POST',
      body: JSON.stringify({ question, ...(area ? areaToRequest(area) : {}) }),
    }),

  /** Water screening of an uploaded photo. Pixel fractions, never km². */
  uploadImage: (file, question = '') => {
    const form = new FormData()
    form.append('image', file)
    if (question) form.append('question', question)
    return upload('/analyze-upload', form)
  },

  /** One stored analysis by id - used to open a single month of a series. */
  analysis: (requestId) => request(`/analyze/${encodeURIComponent(requestId)}`),

  /** Flood extent month by month. Each month is a full cached analysis. */
  series: (payload) =>
    request('/analyze/series', { method: 'POST', body: JSON.stringify(payload) }),

  cacheStats: () => request('/cache'),

  /** The scale a flood GeoTIFF will be delivered at, before downloading it. */
  floodPlan: (planPath) => request(planPath),
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
  latest,
  // {sensor, method?, cloud_limit?} from lib/methods requestFields. Absent
  // means the radar threshold, as before.
  floodChoice,
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
    // Latest-pass mode: the newest Sentinel-1 image sets the dates, so none
    // are sent - and no baseline, which would describe a period nobody chose.
    if (latest) {
      return api.flood({
        ...common, post_start: null, post_end: null, pre_start: null, pre_end: null,
        sensor: 'sentinel-1', latest: true,
      })
    }
    return api.flood({ ...common, sensor: 'sentinel-1', ...(floodChoice || {}) })
  }
  return api.surface({ ...common, analysis_type: analysisType })
}

/**
 * A monthly flood series over the form's From..To range.
 *
 * The sensor is always named. The backend refuses to let cloud cover choose
 * it month by month, because a jump between a radar month and an optical
 * month would be the instrument changing.
 */
export function runSeries({ region, area, postStart, postEnd, scale, floodChoice }) {
  return api.series({
    ...(area ? areaToRequest(area) : { region }),
    start: postStart,
    end: postEnd,
    sensor: floodChoice?.sensor || 'sentinel-1',
    scale: scale || 200,
  })
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
