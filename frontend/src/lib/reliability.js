/**
 * How far to trust an analysis, in words a district officer reads at a glance.
 *
 * Every analysis was scored on places where experts had already drawn the
 * true map: the overlap between our map and theirs (IoU) runs from 0, no
 * overlap, to 1, a perfect match. The backend grades the surface analyses
 * (good / moderate / poor); flood methods carry their IoU, graded here with
 * the same cut-offs the catalogue uses.
 */

export const RELIABILITY_VIEW = {
  good: {
    label: 'Reliable',
    tone: 'good',
    text: 'Matches maps drawn by experts well.',
  },
  moderate: {
    label: 'Fairly reliable',
    tone: 'moderate',
    text: 'Gets the main areas right but misses or adds some.',
  },
  poor: {
    label: 'Rough guide only',
    tone: 'poor',
    text: 'Often wrong in detail. Use it to see the general pattern, not for decisions.',
  },
  unvalidated: {
    label: 'Not scored yet',
    tone: 'unknown',
    text: 'Not yet tested against expert maps, so treat it with care.',
  },
}

export const OVERLAP_EXPLAINER =
  'We tested each analysis on places where experts had already drawn the true map, ' +
  'and measured how much our map overlaps theirs (0 = no overlap, 1 = a perfect match).'

/** Grade an overlap score. Cut-offs: 0.55 and up reliable, 0.4 and up fair. */
export function gradeFromIou(iou) {
  if (typeof iou !== 'number' || !Number.isFinite(iou)) return 'unvalidated'
  if (iou >= 0.55) return 'good'
  if (iou >= 0.4) return 'moderate'
  return 'poor'
}

/**
 * The view for one analysis: {label, tone, text, score}.
 * `grade` wins when the backend supplied one; otherwise it comes from `iou`.
 */
export function reliabilityView({ grade, iou } = {}) {
  const key = RELIABILITY_VIEW[grade] ? grade : gradeFromIou(iou)
  const score = typeof iou === 'number' && Number.isFinite(iou) ? iou : null
  return { ...RELIABILITY_VIEW[key], grade: key, score }
}

/** The sentence under "What does this mean?" for one analysis. */
export function scoreSentence(view) {
  if (view?.score == null) return `${OVERLAP_EXPLAINER} This one has not been scored yet.`
  return `${OVERLAP_EXPLAINER} This one scored ${view.score.toFixed(2)}.`
}
