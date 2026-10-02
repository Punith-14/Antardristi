/**
 * The accuracy table for the How it works panel, built from /analyses only.
 * Nothing here holds a figure of its own.
 */

const pct = (v) => (v == null ? '—' : v)

export function accuracyRows(catalogue) {
  const flood = catalogue?.flood
  const rows = (flood?.methods || []).map((m) => ({
    key: m.key,
    label: m.label,
    iou: pct(m.validation?.iou),
    precision: pct(m.validation?.precision),
    recall: pct(m.validation?.recall),
    scale: m.measured_at_m ? `${m.measured_at_m} m` : '—',
    note: m.caveat || null,
  }))
  const india = flood?.validation?.india
  if (india?.iou != null) {
    rows.push({
      key: 'india',
      label: `Radar threshold on held-out Indian floods (${india.event}, ${india.chips} chips)`,
      iou: india.iou,
      precision: pct(india.precision),
      recall: pct(india.recall),
      scale: '200 m',
      note: india.ci95 ? `95% range ${india.ci95[0]} to ${india.ci95[1]}` : null,
    })
  }
  for (const s of catalogue?.surface || []) {
    rows.push({
      key: `surface_${s.type}`,
      label: s.label,
      iou: pct(s.iou),
      precision: pct(s.precision),
      recall: pct(s.recall),
      scale: '—',
      note: s.iou == null ? 'not validated' : null,
    })
  }
  return rows
}
