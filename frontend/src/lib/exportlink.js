/**
 * The path to a stored analysis's PDF, or null when there is none to link.
 *
 * Kept out of api.js because api.js reads import.meta.env, which only exists
 * under Vite - and this is the part worth testing.
 *
 * Null when there is no usable request_id - a response from before ids
 * existed, or a dry run. (Uploaded images have one now, so they export too.) A button pointing at
 * /analyze/undefined/report.pdf would 404 after the click, which is worse
 * than no button. The id is also checked to be a plain token, because it is
 * placed into a URL path and a stray '/' or '?' would point somewhere else.
 */
const TOKEN = /^[A-Za-z0-9_-]{1,64}$/

export function pdfPath(result) {
  const id = result?.request_id
  if (typeof id !== 'string' || !TOKEN.test(id)) return null

  const path = `/analyze/${id}/report.pdf`

  // A result from /ask carries an ask_id. Passing it on is what puts the
  // question, and the check that the analysis answers it, into the PDF.
  // Without it a PDF of a mismatched answer prints "Verified" - true of the
  // numbers, and misleading about the answer.
  const ask = result?.ask_id
  return typeof ask === 'string' && TOKEN.test(ask) ? `${path}?ask_id=${ask}` : path
}
