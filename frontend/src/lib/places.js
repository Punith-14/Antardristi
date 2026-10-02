/**
 * Villages and roads in the flood zones (D3), shaped for display.
 * The backend looks them up in OpenStreetMap; this only words them, and
 * keeps the caveats attached - a list of names reads as complete otherwise.
 */

const TOKEN = /^[A-Za-z0-9_-]{1,64}$/

/** Worth a lookup: a stored flood result with at least one zone outline. */
export function canLookUpPlaces(result) {
  return typeof result?.request_id === 'string' && TOKEN.test(result.request_id) &&
    (result.evidence || []).some((e) => e.quantity === 'flood_extent') &&
    (result.zones_geojson?.features || []).some((f) => f.properties?.kind === 'outline')
}

export function placesPath(result) {
  return canLookUpPlaces(result) ? `/analyze/${result.request_id}/places` : null
}

/** "12 villages and towns, 18.4 km of main road, in 5 of 23 zones" */
export function placesSummary(block) {
  if (!block || block.error) return null
  const t = block.totals || {}
  const km = Object.values(t.road_km_by_class || {}).reduce((a, b) => a + b, 0)
  const places = t.places === 1 ? '1 village or town' : `${t.places || 0} villages and towns`
  const scope = block.zones_found > block.zones_searched
    ? `in the ${block.zones_searched} listed zones of ${block.zones_found}`
    : `in ${block.zones_searched} zone${block.zones_searched === 1 ? '' : 's'}`
  return `${places} and ${km.toFixed(1)} km of main road crossing the flood zones, ${scope}.`
}

/** One zone's line: names (outside-buffer ones starred) and roads with km. */
export function zoneLine(zone, limit = 8) {
  const names = zone.places.slice(0, limit).map((p) => (p.inside ? p.name : `${p.name}*`))
  const more = zone.places.length - names.length
  const roads = zone.roads.map((r) => `${r.ref || r.name || r.class} ${r.km} km`)
  return {
    places: names.length ? names.join(', ') + (more > 0 ? ` and ${more} more` : '') : 'no named places',
    roads: roads.length ? roads.join(', ') : 'no main roads',
  }
}
