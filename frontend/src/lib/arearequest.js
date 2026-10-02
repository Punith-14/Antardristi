import { circleToRequest, toRequest } from './bbox.js'
import { polygonToRequest } from './polygon.js'

/**
 * A drawn or uploaded area as the API wants it: one of bbox, point +
 * radius_km, polygon, or boundary.
 *
 * Exactly one key comes back. footprint.build refuses a request carrying two
 * shapes rather than picking one, because a caller that sent both could not
 * tell which was measured - so this must never merge them.
 */
export function areaToRequestPure(area) {
  if (!area) return {}
  if (area.kind === 'boundary') return { boundary: area.boundary }
  if (area.kind === 'circle') return circleToRequest(area.centre, area.radiusKm)
  if (area.kind === 'polygon') return polygonToRequest(area.points)
  return toRequest(area.bbox)
}
