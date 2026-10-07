/**
 * The form that produced a stored result, so opening it from History shows
 * what was asked - not the default Kerala, August 2018 left in the form.
 *
 * Read from the job's own request where it has one (analyze, surface,
 * series), and from the result for a question, whose request is only text.
 * A drawn or uploaded area has no name to put back: the Area box is left
 * empty rather than showing a place that was not analysed.
 */

function regionText(job, result) {
  const asked = job?.request?.region
  if (typeof asked === 'string' && asked.trim()) return asked
  const r = result?.region
  if (!r || r.admin_level === 'custom' || !r.name) return ''
  return r.admin_level === 'district' && r.state ? `${r.name}, ${r.state}` : r.name
}

function floodMethodOf(request) {
  if (request?.sensor === 'sentinel-2') return 'optical_threshold'
  if (request?.method && request.method !== 'threshold') return 'radar_change'
  return 'radar_threshold'
}

export function formFromJob(job, defaults) {
  const request = job?.request || {}
  const result = job?.result || {}
  const period = result.period || {}
  const post = period.post || {}
  const pre = period.pre || {}

  if (job?.kind === 'series') {
    return {
      ...defaults, analysisType: 'flood_extent', when: 'monthly',
      region: regionText(job, result),
      postStart: request.start || defaults.postStart, postEnd: request.end || defaults.postEnd,
      preStart: '', preEnd: '',
      floodMethod: floodMethodOf(request),
    }
  }

  const analysisType = job?.kind === 'surface'
    ? (request.analysis_type || result.analysis_type || defaults.analysisType)
    : (result.analysis_type || 'flood_extent')
  // A latest-pass request carries no dates; the result says which were used.
  const postStart = request.post_start || post.start || defaults.postStart
  const postEnd = request.post_end || post.end || defaults.postEnd
  const preStart = request.pre_start || pre.start || ''
  const preEnd = request.pre_end || pre.end || ''

  return {
    ...defaults,
    analysisType,
    region: regionText(job, result),
    when: preStart && preEnd ? 'compare' : 'one',
    postStart, postEnd, preStart, preEnd,
    floodMethod: analysisType === 'flood_extent' ? floodMethodOf(request) : defaults.floodMethod,
  }
}
