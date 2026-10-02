/**
 * Uploaded boundaries (D2), on the client side: what files are accepted, how
 * a chosen shape becomes the area of a request, and how it is described.
 * Parsing, checking and simplifying happen on the server (geo/boundary_upload.py);
 * a browser is not a validator.
 */

export const ACCEPTED = ['.geojson', '.json', '.kml', '.kmz', '.zip']
export const MAX_BYTES = 20 * 1024 * 1024

/** null if the file can be sent, else the reason it cannot. */
export function boundaryFileProblem(file) {
  if (!file) return 'Choose a file.'
  const name = (file.name || '').toLowerCase()
  if (name.endsWith('.shp')) {
    return 'A shapefile is several files (.shp, .shx, .dbf, .prj) - zip them together and upload the .zip.'
  }
  if (!ACCEPTED.some((ext) => name.endsWith(ext))) {
    return `Upload GeoJSON, KML/KMZ or a zipped shapefile (${ACCEPTED.join(', ')}).`
  }
  if (file.size > MAX_BYTES) {
    return `The file is ${(file.size / 1e6).toFixed(1)} MB; the limit is ${MAX_BYTES / 1e6} MB.`
  }
  return null
}

/** The drawn-area object the rest of the app passes around. */
export function boundaryArea(boundary) {
  return { kind: 'boundary', boundary }
}

/** "Alappuzha · uploaded boundary · 1,414 km² (simplified, area -0.02%)" */
export function describeBoundary(area) {
  const source = area?.boundary?.source || {}
  const km2 = source.area_km2 != null ? ` · ${Math.round(source.area_km2).toLocaleString()} km²` : ''
  const simplified = source.simplified ? ` (simplified, area ${source.area_change_pct > 0 ? '+' : ''}${source.area_change_pct}%)` : ''
  return `${source.feature || 'Uploaded shape'} · uploaded boundary${km2}${simplified}`
}

/** GeoJSON [lon, lat] rings -> Leaflet [lat, lng] rings, for Polygon and MultiPolygon. */
export function toLatLngs(geometry) {
  const ring = (r) => r.map(([lon, lat]) => [lat, lon])
  if (!geometry) return []
  if (geometry.type === 'Polygon') return geometry.coordinates.map(ring)
  if (geometry.type === 'MultiPolygon') return geometry.coordinates.map((poly) => poly.map(ring))
  return []
}

/** The picker list for a file with several shapes. */
export function pickerItems(summary) {
  return (summary?.features || []).map((f) => ({
    index: f.index,
    label: f.name,
    detail: f.ok ? `${Math.round(f.info.area_km2).toLocaleString()} km²` : f.error,
    disabled: !f.ok,
  }))
}
