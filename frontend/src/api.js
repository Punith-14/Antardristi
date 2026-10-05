import { areaToRequestPure } from './lib/arearequest'
import { gisDownloads, pdfPath } from './lib/exportlink'
import { waitForJob } from './lib/jobs'

// Built for production, the app is served by the API itself: same origin, so
// no base and no CORS. In development Vite serves it on :5173 and the API is
// on :8000 of the SAME host name - "localhost" and "127.0.0.1" are different
// sites to a browser, and the sign-in cookie would not be sent across them.
const BASE = import.meta.env.VITE_API_BASE ??
  (import.meta.env.PROD ? '' : `${window.location.protocol}//${window.location.hostname}:8000`)

// Every request carries the session cookie and this header. The server
// refuses a cookie-authenticated change without it - a cross-site form
// cannot set it, which is what stops CSRF.
const CSRF = { 'X-Requested-With': 'antardrishti' }

/** Fired when the server says the session has ended, so the app can show sign-in. */
export const SIGNED_OUT = 'antardrishti:signed-out'
function signalSignedOut(status) {
  if (status === 401 && typeof window !== 'undefined') window.dispatchEvent(new Event(SIGNED_OUT))
}

/** See lib/arearequest.js - kept there so node --test can reach it. */
export function areaToRequest(area) {
  return areaToRequestPure(area)
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
    credentials: 'include',
    ...options,
    headers: { 'Content-Type': 'application/json', ...CSRF, ...(options.headers || {}) },
  })

  const body = await response.json().catch(() => ({}))

  if (!response.ok) {
    signalSignedOut(response.status)
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
  const response = await fetch(`${BASE}${path}`, {
    method: 'POST', body: formData, credentials: 'include', headers: CSRF,
  })
  const body = await response.json().catch(() => ({}))
  if (!response.ok) {
    signalSignedOut(response.status)
    const detail = body.detail
    const error = new Error(
      typeof detail === 'string' ? detail : detail?.message || `Upload failed (${response.status})`,
    )
    if (detail && typeof detail === 'object') error.detail = detail
    throw error
  }
  return body
}

/** A report picture's address (the API serves the PNG). */
export function pictureUrl(path) {
  return path && path.startsWith('/pictures/') ? `${BASE}${path}` : null
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
  ask: ({ question, area }, onProgress) =>
    api.runJob('ask', { question, ...(area ? areaToRequest(area) : {}) }, onProgress),

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

  // ---- sign-in and accounts
  me: () => request('/auth/me'),
  login: (username, password) =>
    request('/auth/login', { method: 'POST', body: JSON.stringify({ username, password }) }),
  logout: () => request('/auth/logout', { method: 'POST' }),
  /** Self sign-up: the account works at once, with the access every new account gets. */
  signup: (form) =>
    request('/auth/signup', { method: 'POST', body: JSON.stringify(form) }),
  // ---- account
  resendVerification: (email) => request('/auth/resend', { method: 'POST', body: JSON.stringify({ email }) }),
  verifyEmail: (token) => request('/auth/verify', { method: 'POST', body: JSON.stringify({ token }) }),
  forgotPassword: (email) => request('/auth/forgot', { method: 'POST', body: JSON.stringify({ email }) }),
  resetPassword: (token, password) =>
    request('/auth/reset', { method: 'POST', body: JSON.stringify({ token, password }) }),
  changePassword: (current, next) =>
    request('/auth/password', { method: 'POST', body: JSON.stringify({ current, new: next }) }),
  logoutEverywhere: () => request('/auth/logout-all', { method: 'POST' }),
  activity: () => request('/auth/activity'),
  adminLogins: () => request('/admin/logins'),

  // ---- saving analyses
  saveJob: (jobId, title, note) =>
    request(`/history/${encodeURIComponent(jobId)}/save`, { method: 'POST', body: JSON.stringify({ title, note }) }),
  unsaveJob: (jobId) => request(`/history/${encodeURIComponent(jobId)}/unsave`, { method: 'POST' }),
  editJob: (jobId, change) =>
    request(`/history/${encodeURIComponent(jobId)}`, { method: 'PATCH', body: JSON.stringify(change) }),
  deleteJob: (jobId) => request(`/history/${encodeURIComponent(jobId)}`, { method: 'DELETE' }),

  // ---- report pictures
  pictures: (requestId) => request(`/analyze/${encodeURIComponent(requestId)}/pictures`),
  capturePicture: (requestId, shape) =>
    request(`/analyze/${encodeURIComponent(requestId)}/pictures`, { method: 'POST', body: JSON.stringify(shape) }),
  editPicture: (id, change) =>
    request(`/pictures/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(change) }),
  deletePicture: (id) => request(`/pictures/${encodeURIComponent(id)}`, { method: 'DELETE' }),

  /** Live satellite status for India: orbital elements, ESA's plan, newest image. Public. */
  satellites: () => request('/satellites'),
  /** The same for one state or district. */
  satelliteArea: (region) => request(`/satellites/area?region=${encodeURIComponent(region)}`),
  /** Your finished analyses, newest first, plus this hour's usage. */
  history: (limit = 50) => request(`/history?limit=${limit}`),
  users: () => request('/admin/users'),
  createUser: (user) => request('/admin/users', { method: 'POST', body: JSON.stringify(user) }),
  updateUser: (username, change) =>
    request(`/admin/users/${encodeURIComponent(username)}`, { method: 'PATCH', body: JSON.stringify(change) }),
  deleteUser: (username) => request(`/admin/users/${encodeURIComponent(username)}`, { method: 'DELETE' }),

  // ---- background jobs: start, then poll with progress
  job: (id) => request(`/jobs/${encodeURIComponent(id)}`),
  runJob: async (kind, payload, onProgress) => {
    const { job_id: id } = await request(`/jobs/${kind}`, { method: 'POST', body: JSON.stringify(payload) })
    const result = await waitForJob(() => api.job(id), {
      onProgress, sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
    })
    // Which job produced it, so the result can be saved from where it is shown.
    if (result && typeof result === 'object') result._job = { id, saved: false, title: null, note: null }
    return result
  },

  /** Read an uploaded boundary file: its shapes, checked and simplified. */
  parseBoundary: (file) => {
    const form = new FormData()
    form.append('file', file)
    return upload('/boundary/parse', form)
  },

  /** The finding in Hindi: a checked translation, or the reason there is none. */
  hindi: (path) => request(path),

  /** Villages and roads in the flood zones, from OpenStreetMap. Cached server-side. */
  places: (path) => request(path),

  /** One shape from an already-uploaded boundary file, by index. */
  boundaryFeature: (sha, index) => request(`/boundary/${sha}/${index}`),

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
}, onProgress) {
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
      return api.runJob('analyze', {
        ...common, post_start: null, post_end: null, pre_start: null, pre_end: null,
        sensor: 'sentinel-1', latest: true,
      }, onProgress)
    }
    return api.runJob('analyze', { ...common, sensor: 'sentinel-1', ...(floodChoice || {}) }, onProgress)
  }
  // In the background like flood, so it shows progress and lands in History.
  return api.runJob('surface', { ...common, analysis_type: analysisType }, onProgress)
}

/**
 * A monthly flood series over the form's From..To range.
 *
 * The sensor is always named. The backend refuses to let cloud cover choose
 * it month by month, because a jump between a radar month and an optical
 * month would be the instrument changing.
 */
export function runSeries({ region, area, postStart, postEnd, scale, floodChoice }, onProgress) {
  return api.runJob('series', {
    ...(area ? areaToRequest(area) : { region }),
    start: postStart,
    end: postEnd,
    sensor: floodChoice?.sensor || 'sentinel-1',
    scale: scale || 200,
  }, onProgress)
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
