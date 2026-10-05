/**
 * Where a satellite is, and when it comes near a place - worked out in the
 * browser from published orbital elements (CelesTrak GP data, served by our
 * backend) with SGP4, the standard model those elements are made for.
 *
 * Accuracy: SGP4 from fresh elements is good to about a kilometre for a day
 * or two, plenty for "over the Bay of Bengal" and "passes in 2 h 10 min".
 * A pass is not an image: whether the radar is switched on is ESA's plan,
 * which the backend reads separately.
 */
import { eciToGeodetic, gstime, json2satrec, propagate, degreesLat, degreesLong } from 'satellite.js'

export { countdown, istTime } from './when.js'

const EARTH_RADIUS_KM = 6371.0
const DEG = Math.PI / 180

export function satrecFor(sat) {
  return sat?.omm ? json2satrec(sat.omm) : null
}

/** {lat, lon, altKm, speedKmS} at `date`, or null when SGP4 cannot answer. */
export function stateAt(satrec, date) {
  if (!satrec) return null
  const pv = propagate(satrec, date)
  if (!pv || !pv.position || typeof pv.position === 'boolean') return null
  const geo = eciToGeodetic(pv.position, gstime(date))
  const v = pv.velocity
  return {
    lat: degreesLat(geo.latitude),
    lon: degreesLong(geo.longitude),
    altKm: geo.height,
    speedKmS: v ? Math.sqrt(v.x * v.x + v.y * v.y + v.z * v.z) : null,
  }
}

/** 'Ascending' (moving north) or 'Descending', from two nearby positions. */
export function direction(satrec, date) {
  const a = stateAt(satrec, date)
  const b = stateAt(satrec, new Date(date.getTime() + 30000))
  if (!a || !b) return null
  return b.lat >= a.lat ? 'Ascending' : 'Descending'
}

/** Ground track points from `minutesBack` before to `minutesAhead` after `date`. */
export function track(satrec, date, { minutesBack = 49, minutesAhead = 49, stepS = 60 } = {}) {
  const out = []
  for (let s = -minutesBack * 60; s <= minutesAhead * 60; s += stepS) {
    const st = stateAt(satrec, new Date(date.getTime() + s * 1000))
    if (st) out.push(st)
  }
  return out
}

/** Great-circle distance in km. */
export function distanceKm(a, b) {
  const dLat = (b.lat - a.lat) * DEG
  const dLon = (b.lon - a.lon) * DEG
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * DEG) * Math.cos(b.lat * DEG) * Math.sin(dLon / 2) ** 2
  return 2 * EARTH_RADIUS_KM * Math.asin(Math.min(1, Math.sqrt(h)))
}

/** Every outer ring of a GeoJSON Polygon or MultiPolygon, as [lon, lat] lists. */
export function rings(geometry) {
  if (!geometry) return []
  if (geometry.type === 'Polygon') return geometry.coordinates.slice(0, 1)
  if (geometry.type === 'MultiPolygon') return geometry.coordinates.map((p) => p[0])
  return []
}

function inside(point, ring) {
  let hit = false
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i]
    const [xj, yj] = ring[j]
    if ((yi > point.lat) !== (yj > point.lat) && point.lon < ((xj - xi) * (point.lat - yi)) / (yj - yi) + xi) hit = !hit
  }
  return hit
}

function segmentKm(p, a, b) {
  // Local flat projection around p: fine for the few hundred km that matter.
  const kx = 111.32 * Math.cos(p.lat * DEG)
  const ky = 110.57
  const ax = (a[0] - p.lon) * kx, ay = (a[1] - p.lat) * ky
  const bx = (b[0] - p.lon) * kx, by = (b[1] - p.lat) * ky
  const dx = bx - ax, dy = by - ay
  const t = dx || dy ? Math.max(0, Math.min(1, -(ax * dx + ay * dy) / (dx * dx + dy * dy))) : 0
  return Math.hypot(ax + t * dx, ay + t * dy)
}

/** 0 inside the area, else the distance in km to its nearest edge. */
export function distanceToArea(point, geometry) {
  const list = rings(geometry)
  if (list.some((ring) => inside(point, ring))) return 0
  let best = Infinity
  for (const ring of list) {
    for (let i = 1; i < ring.length; i++) best = Math.min(best, segmentKm(point, ring[i - 1], ring[i]))
  }
  return best
}

/**
 * The next time the satellite comes within `reachKm` of the area, from
 * `from`, searching `hours` ahead: {start, end, closestKm, closestAt,
 * inProgress}, or null. Sentinel-1's radar looks sideways, out to roughly
 * 300 km either side of its track, so that is the default reach.
 */
export function nextPass(satrec, geometry, from = new Date(), { hours = 72, stepS = 30, reachKm = 300 } = {}) {
  if (!satrec || !rings(geometry).length) return null
  let start = null
  let closest = { km: Infinity, at: null }
  const begin = from.getTime()
  for (let t = begin; t <= begin + hours * 3600000; t += stepS * 1000) {
    const st = stateAt(satrec, new Date(t))
    if (!st) continue
    const km = distanceToArea(st, geometry)
    if (km <= reachKm) {
      if (start === null) start = t
      if (km < closest.km) closest = { km, at: t }
    } else if (start !== null) {
      return { start: new Date(start), end: new Date(t), closestKm: Math.round(closest.km),
        closestAt: new Date(closest.at), inProgress: start === begin }
    }
  }
  return null
}

// Seas around India named by box, checked before countries so a point over
// water is named as water. Order matters: the specific seas come first.
const SEAS = [
  { name: 'the Bay of Bengal', lat: [5, 23], lon: [80, 95] },
  { name: 'the Arabian Sea', lat: [5, 25], lon: [51, 77] },
  { name: 'the Andaman Sea', lat: [5, 16], lon: [94, 99] },
  { name: 'the Indian Ocean', lat: [-60, 5], lon: [20, 120] },
  { name: 'the Southern Ocean', lat: [-90, -60], lon: [-180, 180] },
  { name: 'the Arctic Ocean', lat: [75, 90], lon: [-180, 180] },
]

/**
 * A plain name for where a point is: "over India", "over the Bay of Bengal".
 * `countries` is a list of GeoJSON features with properties.name (Natural
 * Earth via world-atlas); land wins over the sea boxes except for the named
 * seas near India, whose boxes include no land of interest.
 */
export function placeLabel(lat, lon, countries = []) {
  for (const f of countries) {
    if (rings(f.geometry).some((ring) => inside({ lat, lon }, ring))) return f.properties?.name || 'land'
  }
  const sea = SEAS.find((s) => lat >= s.lat[0] && lat <= s.lat[1] && lon >= s.lon[0] && lon <= s.lon[1])
  if (sea) return sea.name
  if (lon > 120 || lon < -70) return 'the Pacific Ocean'
  if (lon >= -70 && lon < 20) return 'the Atlantic Ocean'
  return 'open ocean'
}

/** "14.2° N, 86.9° E". */
export function latLonText(lat, lon) {
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return '—'
  return `${Math.abs(lat).toFixed(1)}° ${lat >= 0 ? 'N' : 'S'}, ${Math.abs(lon).toFixed(1)}° ${lon >= 0 ? 'E' : 'W'}`
}

/** The centre of a GeoJSON area's bounding box, as {lat, lon}. */
export function areaCentre(geometry) {
  const pts = rings(geometry).flat()
  if (!pts.length) return null
  const lons = pts.map((p) => p[0])
  const lats = pts.map((p) => p[1])
  return { lon: (Math.min(...lons) + Math.max(...lons)) / 2, lat: (Math.min(...lats) + Math.max(...lats)) / 2 }
}
