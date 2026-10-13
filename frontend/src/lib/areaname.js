/**
 * Naming a shape drawn on the map after the place it sits in:
 * "Circle near Dibrugarh, Assam". A label only - the drawn shape is still
 * what gets measured.
 */

const WORDS = { bbox: 'Box', circle: 'Circle', polygon: 'Drawn area' }

/** {lat, lng} roughly in the middle of a drawn area, or null. */
export function areaCentre(area) {
  if (!area) return null
  if (area.kind === 'circle' && area.centre) return { lat: area.centre.lat, lng: area.centre.lng }
  if (area.kind === 'bbox' && Array.isArray(area.bbox) && area.bbox.length === 4) {
    const [west, south, east, north] = area.bbox
    return { lat: (south + north) / 2, lng: (west + east) / 2 }
  }
  if (area.kind === 'polygon' && Array.isArray(area.points) && area.points.length) {
    const lats = area.points.map((p) => p.lat)
    const lngs = area.points.map((p) => p.lng)
    return {
      lat: (Math.min(...lats) + Math.max(...lats)) / 2,
      lng: (Math.min(...lngs) + Math.max(...lngs)) / 2,
    }
  }
  return null
}

/** "Circle near Dibrugarh, Assam", or a plain description while the name is unknown. */
export function areaTitle(area, placeName) {
  const word = WORDS[area?.kind] || 'Area'
  return placeName ? `${word} near ${placeName}` : `${word} drawn on the map`
}
