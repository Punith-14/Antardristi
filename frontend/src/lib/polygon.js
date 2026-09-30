/**
 * Polygons drawn on the map.
 *
 * The backend has accepted a ring of [longitude, latitude] pairs since
 * geo/footprint.py was written; this is the gesture that produces one.
 *
 * Separated from the Leaflet code for the same reason as bbox.js, only more
 * so. A polygon has two failure modes a rectangle cannot have, and neither
 * one looks like a failure:
 *
 *   - A self-intersecting ring - a bowtie. Earth Engine will happily reduce
 *     over it and return a number. The number is not the area of anything the
 *     user drew, because the two lobes wind in opposite directions and
 *     partially cancel. It is wrong, and it is wrong quietly.
 *
 *   - An area that reads as small while its bounding box is enormous. A thin
 *     diagonal strip from Gujarat to Assam is maybe 20,000 km2 of ground and
 *     a 2,000,000 km2 bounding box. The backend's limit is on the box, so it
 *     refuses - and a message quoting 400,000 km2 at someone looking at a
 *     20,000 km2 sliver is baffling unless it says which number it means.
 *
 * Limits mirror backend/geo/footprint.py. The browser telling you early is a
 * courtesy; the backend refusing is the rule.
 */

// Extension included deliberately: these modules run under `node --test` as
// well as through Vite, and Node's ESM resolver does not guess it.
import { INDIA_BOUNDS, MAX_AREA_KM2, areaKm2, isInsideIndia } from './bbox.js'

/** Matches footprint.MAX_POLYGON_POINTS. */
export const MAX_POLYGON_POINTS = 500

/** A ring needs three corners before it encloses anything. */
export const MIN_POLYGON_POINTS = 3

const EARTH_RADIUS_KM = 6371

/** How near the first vertex a click must land, in screen pixels, to close. */
export const SNAP_PIXELS = 12

/**
 * The [west, south, east, north] box a ring sits inside.
 *
 * This is the box the backend measures against its area limit, so it is the
 * one the user has to be told about when a shape is refused.
 */
export function polygonBounds(points) {
  if (!Array.isArray(points) || points.length === 0) return null

  // Optional chaining is not decoration: a half-built ring can hold a null
  // where a point has not landed yet, and reading .lng off it throws inside a
  // render pass - which takes the whole map down rather than showing an error.
  const lngs = points.map((p) => p?.lng)
  const lats = points.map((p) => p?.lat)
  if (![...lngs, ...lats].every(Number.isFinite)) return null

  return [Math.min(...lngs), Math.min(...lats), Math.max(...lngs), Math.max(...lats)]
}

/**
 * Area of the ring itself, in square kilometres, on a sphere.
 *
 * Not the shoelace formula on raw degrees. That treats a degree of longitude
 * as a fixed width, and at 34 N in Ladakh a degree of longitude is 17% shorter
 * than one of latitude - the same error that made the circle tool use
 * haversine rather than flat Pythagoras.
 *
 * This is the standard spherical-excess sum:
 *
 *     A = R^2 / 2 * | sum over edges of (d_lambda)(sin phi_i + sin phi_i+1) |
 *
 * Absolute value because the sign only records winding direction, and a user
 * dragging clockwise means exactly what one dragging anticlockwise means.
 */
export function polygonAreaKm2(points) {
  if (!Array.isArray(points) || points.length < MIN_POLYGON_POINTS) return 0

  const toRad = (deg) => (deg * Math.PI) / 180
  let total = 0

  for (let i = 0; i < points.length; i += 1) {
    const a = points[i]
    const b = points[(i + 1) % points.length]      // wraps, closing the ring
    if (!Number.isFinite(a?.lat) || !Number.isFinite(b?.lat)) return 0

    total +=
      (toRad(b.lng) - toRad(a.lng)) * (Math.sin(toRad(a.lat)) + Math.sin(toRad(b.lat)))
  }

  return Math.abs((total * EARTH_RADIUS_KM * EARTH_RADIUS_KM) / 2)
}

/** Whether two line segments cross, endpoints excluded. */
function segmentsCross(p1, p2, p3, p4) {
  if (![p1, p2, p3, p4].every((p) => Number.isFinite(p?.lat) && Number.isFinite(p?.lng))) {
    return false
  }

  const cross = (o, a, b) =>
    (a.lng - o.lng) * (b.lat - o.lat) - (a.lat - o.lat) * (b.lng - o.lng)

  const d1 = cross(p3, p4, p1)
  const d2 = cross(p3, p4, p2)
  const d3 = cross(p1, p2, p3)
  const d4 = cross(p1, p2, p4)

  // Strict signs only. Touching at a shared endpoint is how a ring is built,
  // not a crossing, so collinear and zero cases are deliberately not counted.
  return (
    ((d1 > 0 && d2 < 0) || (d1 < 0 && d2 > 0)) &&
    ((d3 > 0 && d4 < 0) || (d3 < 0 && d4 > 0))
  )
}

/**
 * Whether the ring crosses itself.
 *
 * O(n^2), which is fine: the ring is capped at 500 points and this runs once
 * on mouse-up, not on every mouse-move.
 */
export function selfIntersects(points) {
  if (!Array.isArray(points) || points.length < 4) return false

  const n = points.length
  for (let i = 0; i < n; i += 1) {
    const a1 = points[i]
    const a2 = points[(i + 1) % n]

    for (let j = i + 1; j < n; j += 1) {
      // Skip the neighbouring edge, and the wrap-around pair that shares the
      // first vertex with the last edge.
      if (j === i || j === (i + 1) % n || (i === 0 && j === n - 1)) continue

      if (segmentsCross(a1, a2, points[j], points[(j + 1) % n])) return true
    }
  }
  return false
}

/**
 * Whether this polygon can be sent, and if not, why.
 *
 * Returns { ok, reason }, same shape as validate() and validateCircle().
 */
export function validatePolygon(points) {
  if (!Array.isArray(points) || points.length < MIN_POLYGON_POINTS) {
    const have = Array.isArray(points) ? points.length : 0
    return {
      ok: false,
      reason: `A polygon needs at least ${MIN_POLYGON_POINTS} points. You have ${have}.`,
    }
  }
  if (points.length > MAX_POLYGON_POINTS) {
    return {
      ok: false,
      reason: `That shape has ${points.length} points, over the ${MAX_POLYGON_POINTS} limit. Draw it with fewer corners.`,
    }
  }
  if (selfIntersects(points)) {
    return {
      ok: false,
      reason: 'The outline crosses itself, so it does not enclose a single area. Undo the last point and try again.',
    }
  }

  const bounds = polygonBounds(points)
  if (!bounds) {
    return { ok: false, reason: 'Some of those points are not valid coordinates.' }
  }
  if (!isInsideIndia(bounds)) {
    return { ok: false, reason: 'The area must be over India.' }
  }

  // The backend checks the bounding box, not the ring. Mirroring that here is
  // the point - but the message has to name the box, or someone looking at a
  // narrow 20,000 km2 strip is told it exceeds a 400,000 km2 limit and has no
  // way to work out why.
  const boxArea = areaKm2(bounds)
  if (boxArea > MAX_AREA_KM2) {
    return {
      ok: false,
      reason:
        `That shape spans ${Math.round(boxArea).toLocaleString()} km² corner to corner, ` +
        `over the ${MAX_AREA_KM2.toLocaleString()} km² limit — even though the outline ` +
        `itself is about ${Math.round(polygonAreaKm2(points)).toLocaleString()} km². ` +
        'Draw it in smaller pieces.',
    }
  }

  return { ok: true, reason: null }
}

/** Short label for the toolbar, e.g. "7 points · 2,140 km²". */
export function describePolygon(points) {
  if (!Array.isArray(points) || points.length < MIN_POLYGON_POINTS) return ''

  const area = Math.round(polygonAreaKm2(points)).toLocaleString()
  const bounds = polygonBounds(points)
  const lat = ((bounds[1] + bounds[3]) / 2).toFixed(2)
  const lng = ((bounds[0] + bounds[2]) / 2).toFixed(2)

  return `${points.length} points · ${area} km² · ${lng} E, ${lat} N`
}

/**
 * The request body fragment the API expects: an open ring of [lng, lat].
 *
 * Open, not closed - from_polygon closes it server-side, and sending a
 * duplicated last point would make the reported point count one too high.
 */
export function polygonToRequest(points) {
  return {
    polygon: points.map((p) => [
      Number(p.lng.toFixed(5)),
      Number(p.lat.toFixed(5)),
    ]),
  }
}

/**
 * Whether a click at `point` is close enough to `target` to mean "close the
 * ring", in screen pixels rather than degrees.
 *
 * Pixels because that is what the user is aiming with. A fixed tolerance in
 * degrees would be a forgiving target when zoomed out and an impossible one
 * when zoomed in.
 */
export function withinSnap(point, target, threshold = SNAP_PIXELS) {
  if (!point || !target) return false
  const dx = point.x - target.x
  const dy = point.y - target.y
  return Math.sqrt(dx * dx + dy * dy) <= threshold
}

/**
 * What a keypress means while drawing: 'finish', 'undo', 'cancel' or null.
 *
 * Null for anything else, so keys this tool does not own keep working - Tab
 * in particular, which otherwise stops escaping the map.
 */
export function actionFromKey(key) {
  if (key === 'Enter') return 'finish'
  if (key === 'Escape') return 'cancel'
  if (key === 'Backspace' || key === 'Delete') return 'undo'
  return null
}

/** One line of guidance, changing as the ring grows. */
export function drawingHint(points) {
  const count = Array.isArray(points) ? points.length : 0

  if (count === 0) return 'Click on the map to place the first corner.'
  if (count < MIN_POLYGON_POINTS) {
    const needed = MIN_POLYGON_POINTS - count
    return `${count} placed. ${needed} more before this encloses an area. Backspace undoes.`
  }
  return `${count} points. Click the first one, or press Enter, to close the shape. Backspace undoes, Escape cancels.`
}

export { INDIA_BOUNDS }
