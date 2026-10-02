/**
 * Background jobs (backend core/jobs.py): start, then poll until done.
 * Pure - fetch and sleep are passed in - so node --test covers the logic.
 */

export const POLL_MS = 1500
export const MAX_WAIT_MS = 15 * 60 * 1000

/**
 * Poll `getJob()` until the job is done or failed.
 * Calls onProgress(steps) whenever the step list grows.
 * Resolves with the result; rejects with an Error carrying the job's detail.
 */
export async function waitForJob(getJob, { onProgress, sleep, now = () => Date.now(), pollMs = POLL_MS, maxWaitMs = MAX_WAIT_MS } = {}) {
  const started = now()
  let seen = -1
  for (;;) {
    const job = await getJob()
    if (onProgress && (job.steps || []).length !== seen) {
      seen = (job.steps || []).length
      onProgress(job.steps || [], job.status)
    }
    if (job.status === 'done') return job.result
    if (job.status === 'failed') throw jobError(job.error)
    if (now() - started > maxWaitMs) {
      throw new Error('The analysis is taking longer than 15 minutes. It may still finish - check Recent later.')
    }
    await sleep(pollMs)
  }
}

/** The job's recorded failure as an Error the panels already know how to show. */
export function jobError(error) {
  const detail = error?.detail
  const message = typeof detail === 'string' ? detail : detail?.message || 'The analysis failed.'
  const err = new Error(message)
  err.status = error?.status
  if (detail && typeof detail === 'object') {
    err.code = detail.error
    err.matches = detail.matches
    err.detail = detail
  }
  return err
}

/** The line shown while a job runs: the latest step, numbered. */
export function progressLine(steps, status) {
  if (status === 'queued' || !steps?.length) return 'Waiting for a free worker…'
  return `Step ${steps.length}: ${steps[steps.length - 1].text}…`
}
