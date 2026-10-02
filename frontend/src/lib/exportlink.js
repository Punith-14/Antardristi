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

/**
 * GIS downloads for a stored flood result (B4), or [] when there are none.
 *
 * Only flood results - their evidence carries `flood_extent` - have zones
 * and a flood map to export. The district table only when there is one, and
 * the flood map only when something was observed: a link that 404s or 409s
 * after the click is worse than no link.
 */
export function gisDownloads(result) {
  const id = result?.request_id
  if (typeof id !== 'string' || !TOKEN.test(id)) return []
  const isFlood = (result.evidence || []).some((e) => e.quantity === 'flood_extent')
  if (!isFlood) return []

  const base = `/analyze/${id}/export`
  const items = [
    { key: 'geojson', label: 'Zones · GeoJSON', hint: 'QGIS, ArcGIS', path: `${base}/zones.geojson` },
    { key: 'kml', label: 'Zones · KML', hint: 'Google Earth', path: `${base}/zones.kml` },
    { key: 'zones_csv', label: 'Zones · CSV', hint: 'spreadsheet', path: `${base}/zones.csv` },
  ]
  if (result.districts?.rows?.length) {
    items.push({ key: 'districts_csv', label: 'Districts · CSV', hint: 'spreadsheet', path: `${base}/districts.csv` })
  }
  if (result.observation?.sensor_used) {
    items.push({
      key: 'geotiff',
      label: 'Flood map · GeoTIFF',
      hint: 'zipped with its legend; rebuilt on Earth Engine, takes a minute',
      path: `${base}/flood.zip`,
      planPath: `${base}/flood-plan`,
    })
  }
  return items
}

/** The line under the GeoTIFF link once its plan is known. */
export function planLine(plan) {
  if (!plan) return null
  if (plan.scale_m == null) return plan.note || 'Too large to download as one file.'
  return plan.note
}
