/**
 * Wording and validation for the two entry points that were API-only:
 * asking a question in plain language, and uploading an image.
 *
 * Both backends were finished and tested before either had a way in from the
 * page. The project's title calls it a conversational platform; until these
 * existed, nobody using the site could hold the conversation.
 *
 * Kept out of the components so the rules are tested rather than eyeballed -
 * above all the rule that a failed question check is shown ABOVE the finding,
 * not beneath it, because a correct-looking answer to the wrong question is
 * the failure the whole check exists for.
 */

/** Matches rgb_upload.MAX_UPLOAD_BYTES. The backend still enforces it. */
export const MAX_UPLOAD_BYTES = 20 * 1024 * 1024

/** Matches the extensions /analyze-upload accepts. */
export const UPLOAD_EXTENSIONS = ['.png', '.jpg', '.jpeg', '.webp']

export const EXAMPLE_QUESTIONS = [
  'Show flooding in Kerala in August 2018',
  'How much did flooding increase in Kerala in August 2018 compared to May 2018?',
  'Vegetation health in Punjab during kharif 2023',
]

/** A question worth sending: trimmed, non-empty, not absurdly long. */
export function validateQuestion(text) {
  const question = (text || '').trim()
  if (!question) return { ok: false, reason: 'Type a question first.' }
  if (question.length < 8) {
    return { ok: false, reason: 'That is too short to work out what to measure, where and when.' }
  }
  if (question.length > 500) {
    return { ok: false, reason: 'Keep the question under 500 characters.' }
  }
  return { ok: true, reason: null, question }
}

/**
 * Whether a file can be uploaded, and if not, why - before the request.
 *
 * The same checks the backend makes. Checking here only saves a round trip;
 * the server refuses anyway, because a browser is not a validator.
 */
export function validateUpload(file) {
  if (!file) return { ok: false, reason: 'Choose an image first.' }

  const name = (file.name || '').toLowerCase()
  const dot = name.lastIndexOf('.')
  const extension = dot >= 0 ? name.slice(dot) : ''
  if (!UPLOAD_EXTENSIONS.includes(extension)) {
    return { ok: false, reason: 'Upload a PNG, JPG, JPEG or WEBP image.' }
  }
  if (!file.size) return { ok: false, reason: 'That file is empty.' }
  if (file.size > MAX_UPLOAD_BYTES) {
    const mb = (file.size / 1024 / 1024).toFixed(1)
    return { ok: false, reason: `That file is ${mb} MB, over the 20 MB limit.` }
  }
  return { ok: true, reason: null }
}

/**
 * The question check, as the result panel shows it. Null when there is none.
 *
 * Failures come first and are worded as what went wrong, because this block
 * sits above the finding and someone skimming reads only the top of it.
 */
export function alignmentView(alignment) {
  if (!alignment || !Array.isArray(alignment.checks)) return null

  const total = alignment.checks.length
  const passed = alignment.checks.filter((c) => c.passed).length
  const failures = alignment.failures?.length
    ? alignment.failures
    : alignment.checks.filter((c) => !c.passed).map((c) => c.detail)

  if (alignment.passed && failures.length === 0) {
    return {
      tone: 'ok',
      headline: `The analysis matches your question (${passed} of ${total} checks).`,
      failures: [],
    }
  }
  return {
    tone: 'warn',
    headline: 'This may not answer your question. Read this before the finding.',
    failures,
  }
}

/**
 * A refused question, turned into something a person can act on.
 *
 * /ask refuses with a structured detail - what it understood, and example
 * questions or a hint. The generic error handler kept only the message, so a
 * user was told "No Indian region was named" and nothing about how to fix it.
 */
export function askErrorView(error) {
  if (!error) return null
  const detail = error.detail && typeof error.detail === 'object' ? error.detail : {}
  return {
    message: detail.message || error.message || 'The question could not be answered.',
    understood: detail.understood || null,
    hint: detail.hint || null,
    suggestions: Array.isArray(detail.try) ? detail.try : [],
  }
}

export function isUpload(result) {
  return result?.analysis_type === 'uploaded_image_screening'
}

/**
 * The line under the coverage bar: where the data came from.
 *
 * Previously anything that was not Sentinel-1 was labelled "Sentinel-2
 * optical", which would have described a phone photo as satellite imagery.
 */
export function sourceLine(observation) {
  if (!observation) return ''
  const sensor = observation.sensor_used
  if (sensor === 'sentinel-1') {
    return `Sentinel-1 radar · ${observation.scenes_used}/${observation.scenes_available} scenes used`
  }
  if (sensor === 'sentinel-2') {
    return `Sentinel-2 optical · ${observation.scenes_used}/${observation.scenes_available} scenes used`
  }
  if (sensor === 'uploaded RGB image') {
    const size = observation.image_width_px
      ? ` · ${observation.image_width_px} × ${observation.image_height_px} px`
      : ''
    return `Uploaded image${size} · not georeferenced`
  }
  return sensor || ''
}

/**
 * Whether a coverage figure exists at all.
 *
 * A photo has no footprint, so "100% of the region observed" - the old
 * default when coverage_fraction was missing - was a claim about ground the
 * image does not locate.
 */
export function hasCoverage(observation) {
  return Number.isFinite(observation?.coverage_fraction)
}

/** Header date line; empty rather than " to " when there is no period. */
export function periodLine(period) {
  const post = period?.post
  if (!post?.start) return ''
  const base = `${post.start} to ${post.end}`
  return period.pre?.start ? `${base} · baseline ${period.pre.start}` : base
}

/** Who wrote the report, without ever printing "null". */
export function writtenBy(report) {
  if (!report) return ''
  if (report.generator_model) return `Written by ${report.generator_model}`
  if (report.written_by) return `Written by ${report.written_by}`
  return report.fallback_used ? 'Written from a template' : ''
}
