/**
 * The Hindi finding (D4): a checked translation of the verified English.
 * The English stays authoritative; Hindi is shown only when it passed.
 */

const TOKEN = /^[A-Za-z0-9_-]{1,64}$/

/** Only a verified English report is offered in Hindi. */
export function hindiPath(result) {
  if (!result?.report?.text || !result?.verification?.passed) return null
  return typeof result.request_id === 'string' && TOKEN.test(result.request_id)
    ? `/analyze/${result.request_id}/report/hi` : null
}

/** The PDF link with the Hindi finding added, keeping any ask_id. */
export function withHindi(pdfHref) {
  if (!pdfHref) return null
  return `${pdfHref}${pdfHref.includes('?') ? '&' : '?'}lang=hi`
}

export const HINDI_LABEL =
  'मशीन अनुवाद - Machine translation of the verified English report, checked sentence by ' +
  'sentence: every number, every [E#] citation and every caveat kept. The English is authoritative.'

/** What to show: {text} when the check passed, {reason} when it did not. */
export function hindiView(block) {
  if (!block) return null
  if (block.available && block.text) return { text: block.text }
  return { reason: block.reason || 'The Hindi translation is not available.' }
}
