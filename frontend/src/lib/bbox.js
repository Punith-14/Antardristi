/**
 * Rectangles drawn on the map.
 *
 * Separated from the Leaflet code because the errors here are arithmetic and
 * invisible: a box dragged right-to-left has its corners the wrong way round,
 * and the backend rejects it with a message about west being greater than
 * east. Leaflet reports what the mouse did; this turns it into the
 * [west, south, east, north] order the API expects, whichever way the drag
 * went.
 *
 * The limits mirror backend/footprint.py deliberately. Duplicating them means
 * the user hears "too big" while still holding the mouse rather than after a
 * round trip - but the backend still enforces them, because a browser is not
 * a validator.
 */

/** Matches footprint.MAX_AREA_KM2. */
export const MAX_AREA_KM2 = 400000

/** Matches footprint.MIN_SPAN_DEG. */
export const MIN_SPAN_DEG = 0.001

/** Matches footprint.INDIA_BOUNDS: west, south, east, north. */
export const INDIA_BOUNDS = [67.0, 6.0, 98.5, 37.5]

/**
 * Two dragged corners into [west, south, east, north].
 *
 * Either corner may be either end of the drag - dragging up-and-left is as
 * natural as down-and-right, and both must produce the same box.
 */
export function normaliseBounds(a, b) {
  if (!a || !b) return null

  const west = Math.min(a.lng, b.lng)
  const east = Math.max(a.lng, b.lng)
  const south = Math.min(a.lat, b.lat)
  const north = Math.max(a.lat, b.lat)

  if (![west, south, east, north].every(Number.isFinite)) return null
  return [west, south, east, north]
}

/** Rough area, matching the backend's cosine-scaled estimate. */
export function areaKm2(bbox) {
  if (!bbox || bbox.length !== 4) return 0
  const [west, south, east, north] = bbox

  const mid = ((south + north) / 2) * (Math.PI / 180)
  const height = (north - south) * 111.32
  const width = (east - west) * 111.32 * Math.cos(mid)
  return Math.abs(height * width)
}

export function isInsideIndia(bbox) {
  if (!bbox || bbox.length !== 4) return false
  const [west, south, east, north] = bbox
  const [minLon, minLat, maxLon, maxLat] = INDIA_BOUNDS

  return (
    west >= minLon && east <= maxLon && south >= minLat && north <= maxLat
  )
}

/**
 * Whether this box can be sent, and if not, why - in words a user can act on.
 *
 * Returns { ok, reason }. A silent refusal would leave someone dragging the
 * same box repeatedly wondering why nothing happens.
 */
export function validate(bbox) {
  if (!bbox || bbox.length !== 4) {
    return { ok: false, reason: 'Draw a rectangle first.' }
  }

  const [west, south, east, north] = bbox
  if (east - west < MIN_SPAN_DEG || north - south < MIN_SPAN_DEG) {
    return { ok: false, reason: 'That area is too small to measure. Drag a larger box.' }
  }
  if (!isInsideIndia(bbox)) {
    return { ok: false, reason: 'The area must be over India.' }
  }

  const area = areaKm2(bbox)
  if (area > MAX_AREA_KM2) {
    return {
      ok: false,
      reason: `That area is about ${Math.round(area).toLocaleString()} km², over the ${MAX_AREA_KM2.toLocaleString()} km² limit.`,
    }
  }

  return { ok: true, reason: null }
}

/** Short label for the toolbar, e.g. "2,140 km² · 76.01–77.44 E". */
export function describe(bbox) {
  if (!bbox || bbox.length !== 4) return ''
  const [west, south, east, north] = bbox
  const area = Math.round(areaKm2(bbox)).toLocaleString()
  return `${area} km² · ${west.toFixed(2)}–${east.toFixed(2)} E, ${south.toFixed(2)}–${north.toFixed(2)} N`
}

/** The request body fragment the API expects. */
export function toRequest(bbox) {
  // Rounded because six decimals of longitude is a tenth of a millimetre, and
  // the coordinates come from a mouse.
  return { bbox: bbox.map((value) => Number(value.toFixed(5))) }
}


/* ------------------------------------------------------------------ circles
 *
 * A circle is the shape people actually mean for "around this town", and the
 * backend has taken point + radius_km from the start. Drawn by dragging out
 * from a centre, so the maths is a distance rather than two corners.
 */

/** Matches footprint.MIN_RADIUS_KM and MAX_RADIUS_KM. */
export const MIN_RADIUS_KM = 0.5
export const MAX_RADIUS_KM = 300

const EARTH_RADIUS_KM = 6371

/**
 * Great-circle distance between two lat/lng points, in kilometres.
 *
 * Haversine rather than flat Pythagoras on degrees: at 34 N a degree of
 * longitude is 17% shorter than a degree of latitude, so treating them as
 * equal would make a circle drawn in Ladakh come out a fifth too large.
 */
export function distanceKm(a, b) {
  if (!a || !b) return 0

  const toRad = (deg) => (deg * Math.PI) / 180
  const dLat = toRad(b.lat - a.lat)
  const dLng = toRad(b.lng - a.lng)
  const lat1 = toRad(a.lat)
  const lat2 = toRad(b.lat)

  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLng / 2) ** 2

  return 2 * EARTH_RADIUS_KM * Math.asin(Math.min(1, Math.sqrt(h)))
}

export function circleAreaKm2(radiusKm) {
  if (!Number.isFinite(radiusKm) || radiusKm <= 0) return 0
  return Math.PI * radiusKm * radiusKm
}

/** Whether this circle can be sent, and if not, why. */
export function validateCircle(centre, radiusKm) {
  if (!centre || !Number.isFinite(radiusKm)) {
    return { ok: false, reason: 'Drag out from a centre point first.' }
  }
  if (radiusKm < MIN_RADIUS_KM) {
    return { ok: false, reason: 'That circle is too small to measure. Drag further out.' }
  }
  if (radiusKm > MAX_RADIUS_KM) {
    return {
      ok: false,
      reason: `Radius is about ${Math.round(radiusKm)} km, over the ${MAX_RADIUS_KM} km limit.`,
    }
  }
  // Reuse the bbox check on the circle's enclosing square, so a circle cannot
  // sit over India's centre while spilling past the bounds the backend keeps.
  const degLat = radiusKm / 111.32
  const degLng = radiusKm / (111.32 * Math.cos((centre.lat * Math.PI) / 180) || 1)
  const enclosing = [
    centre.lng - degLng,
    centre.lat - degLat,
    centre.lng + degLng,
    centre.lat + degLat,
  ]
  if (!isInsideIndia(enclosing)) {
    return { ok: false, reason: 'The area must be over India.' }
  }

  return { ok: true, reason: null }
}

export function describeCircle(centre, radiusKm) {
  if (!centre || !Number.isFinite(radiusKm)) return ''
  const area = Math.round(circleAreaKm2(radiusKm)).toLocaleString()
  return `${radiusKm.toFixed(1)} km radius · ${area} km² · ${centre.lng.toFixed(2)} E, ${centre.lat.toFixed(2)} N`
}

/**
 * A short label for a result whose area was drawn, from the response's own
 * region block.
 *
 * Without this every drawn analysis reads "Flood extent · user-defined
 * rectangle" in the history list, so three of them in a row are
 * indistinguishable - which defeats the point of a history. Uses the centre
 * and size, since that is what actually tells two drawn areas apart.
 */
export function describeFootprint(region) {
  const shape = region?.footprint
  if (!shape) return region?.name || ''

  if (shape.kind === 'circle' && Array.isArray(shape.centre)) {
    const [lng, lat] = shape.centre
    return `${shape.radius_km} km around ${lat.toFixed(2)} N, ${lng.toFixed(2)} E`
  }

  // An uploaded boundary is named by the shape the user picked from the file.
  if (shape.kind === 'boundary') return region.name || 'uploaded boundary'

  // A polygon carries a bbox too, so without this branch it would fall through
  // below and be labelled by its bounding box - describing an outline by the
  // rectangle around it, which is not the area that was measured.
  if (shape.kind === 'polygon' && Array.isArray(shape.bbox)) {
    const [west, south, east, north] = shape.bbox
    const lat = ((south + north) / 2).toFixed(2)
    const lng = ((west + east) / 2).toFixed(2)
    return `${shape.points}-point outline at ${lat} N, ${lng} E`
  }

  const box = shape.bbox
  if (Array.isArray(box) && box.length === 4) {
    const [west, south, east, north] = box
    const lat = (south + north) / 2
    const lng = (west + east) / 2
    const area = Math.round(shape.approx_area_km2 ?? areaKm2(box)).toLocaleString()
    return `${area} km² at ${lat.toFixed(2)} N, ${lng.toFixed(2)} E`
  }

  return region?.name || ''
}

export function circleToRequest(centre, radiusKm) {
  return {
    point: [Number(centre.lng.toFixed(5)), Number(centre.lat.toFixed(5))],
    radius_km: Number(radiusKm.toFixed(2)),
  }
}
