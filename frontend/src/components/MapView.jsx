import { useCallback, useEffect, useRef, useState } from 'react'

import {
  describe as describeBbox,
  describeCircle,
  distanceKm,
  normaliseBounds,
  validate,
  validateCircle,
} from '../lib/bbox'
import {
  actionFromKey,
  describePolygon,
  drawingHint,
  validatePolygon,
  withinSnap,
} from '../lib/polygon'
import { ACCEPTED, describeBoundary, pickerItems, toLatLngs } from '../lib/boundary'
import {
  clipInset,
  percentFromPointer,
  periodLabel,
  stepFromKey,
  swipeLayers,
} from '../lib/swipe'

/**
 * Leaflet map. Plain Leaflet through a ref rather than react-leaflet, to avoid
 * a wrapper library that has to keep pace with React 19.
 *
 * Four layers, in the order they matter:
 *   1. OpenStreetMap basemap - this is what makes a result legible. "Flooding
 *      around Kuttanad" means something; "flooding at 9.51 N" does not.
 *   2. Baseline water, in comparison mode only - where water already was.
 *   3. Earth Engine raster overlay - every detected pixel.
 *   4. Zone polygons and markers - discrete affected areas, clickable.
 *
 * Which of 3 and 4 leads is decided by the backend and arrives as `mapHints`.
 *
 * When the backend supplies baseline tiles, 2 and 3 are stacked and 3 is
 * clipped by a draggable divider, so dragging left wipes the flood away and
 * shows what the same ground looked like before. Both windows use the same
 * threshold and relative orbit, so the difference the user sees is water and
 * not a change of viewing geometry - which is the only reason this comparison
 * is honest enough to put on screen.
 */

const INDIA_CENTRE = [22.0, 79.0]
const SEVERITY_COLOURS = { high: '#d7301f', moderate: '#fc8d59', low: '#fdcc8a' }

export default function MapView({
  result,
  selectedZone,
  onSelectZone,
  drawnArea,
  onDrawArea,
  onUploadBoundary,
  boundaryChoices,
  onPickBoundary,
  boundaryMessage,
}) {
  const fileRef = useRef(null)
  const containerRef = useRef(null)
  const mapRef = useRef(null)
  const layersRef = useRef({ overlay: null, baseline: null, terrain: null, zones: null, markers: null })
  const draggingRef = useRef(false)
  const drawRef = useRef({ active: false, origin: null, rectangle: null })
  // The in-progress polygon's Leaflet layers, kept out of state: they change
  // on every click and re-rendering the whole map for a preview line would
  // tear down the tile layer.
  const ringRef = useRef({ outline: null, vertices: [], points: [] })

  const [drawing, setDrawing] = useState(null)   // null | 'box' | 'circle' | 'polygon'
  const [drawError, setDrawError] = useState('')
  // Committed vertices of the polygon being drawn. State rather than a ref
  // because the hint line and the point count are rendered from it.
  const [ring, setRing] = useState([])

  const [swipe, setSwipe] = useState(50)

  // Derived, not state. Storing it would mean setting it from inside the
  // redraw effect, which costs a second render pass on every result.
  const { enabled: swipeEnabled } = swipeLayers(result?.artifacts)

  // Create the map once.
  useEffect(() => {
    if (mapRef.current || !containerRef.current || !window.L) return

    const map = window.L.map(containerRef.current, {
      center: INDIA_CENTRE,
      zoom: 5,
      zoomControl: true,
    })

    window.L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; OpenStreetMap contributors',
      maxZoom: 19,
    }).addTo(map)

    mapRef.current = map
    return () => {
      map.remove()
      mapRef.current = null
    }
  }, [])

  // Redraw whenever a new result arrives.
  useEffect(() => {
    const map = mapRef.current
    if (!map || !window.L) return

    Object.values(layersRef.current).forEach((layer) => {
      if (layer) map.removeLayer(layer)
    })
    layersRef.current = { overlay: null, baseline: null, terrain: null, zones: null, markers: null }

    if (!result) return

    const hints = result.map || {}
    const { before: baselineUrl, after: tileUrl } = swipeLayers(result.artifacts)

    // Unreliable results are drawn faintly. A method scoring IoU 0.057 must not
    // look as confident on screen as one scoring 0.888.
    const opacity =
      hints.overlay_style === 'hatched'
        ? 0.35
        : hints.overlay_style === 'translucent'
          ? 0.6
          : 0.8

    // Baseline first so it sits underneath: Leaflet stacks tile layers in the
    // order they are added, and the clipped layer has to be the top one.
    if (baselineUrl && tileUrl) {
      layersRef.current.baseline = window.L.tileLayer(baselineUrl, {
        opacity,
        attribution: 'Google Earth Engine',
      }).addTo(map)
    }

    if (tileUrl) {
      layersRef.current.overlay = window.L.tileLayer(tileUrl, {
        opacity,
        attribution: 'Google Earth Engine',
      }).addTo(map)
    }

    // Dark ground the terrain check did not count, in its own colour: a real
    // flood in the hills that the rule removed must still be visible.
    const terrainUrl = result.artifacts?.terrain_excluded_tiles
    if (terrainUrl) {
      layersRef.current.terrain = window.L.tileLayer(terrainUrl, {
        opacity: 0.55,
        attribution: 'Google Earth Engine',
      }).addTo(map)
    }

    const geojson = result.zones_geojson
    if (geojson?.features?.length) {
      const outlines = {
        type: 'FeatureCollection',
        features: geojson.features.filter((f) => f.properties?.kind === 'outline'),
      }
      const markers = geojson.features.filter((f) => f.properties?.kind === 'marker')

      if (outlines.features.length) {
        layersRef.current.zones = window.L.geoJSON(outlines, {
          style: (feature) => ({
            color: SEVERITY_COLOURS[feature.properties.severity] || '#3182bd',
            weight: 2,
            opacity: 0.9,
            fillOpacity: 0.25,
          }),
          onEachFeature: (feature, layer) => {
            const p = feature.properties
            layer.bindPopup(
              `<strong>${p.id}</strong><br/>${p.area_km2.toLocaleString()} km²<br/>` +
                `<span style="color:${p.colour}">${p.severity}</span> · rank ${p.rank}`,
            )
            layer.on('click', () => onSelectZone?.(p.id))
          },
        }).addTo(map)
      }

      if (markers.length && hints.show_zone_labels) {
        layersRef.current.markers = window.L.layerGroup(
          markers.map((feature) => {
            const [lon, lat] = feature.geometry.coordinates
            const p = feature.properties
            return window.L.marker([lat, lon], {
              icon: window.L.divIcon({
                className: 'zone-pin',
                html: `<span style="background:${p.colour}">${p.rank}</span>`,
                iconSize: [22, 22],
              }),
            }).on('click', () => onSelectZone?.(p.id))
          }),
        ).addTo(map)
      }

      if (layersRef.current.zones && hints.fit_bounds_to === 'zones') {
        map.fitBounds(layersRef.current.zones.getBounds(), { padding: [30, 30] })
        return
      }
    }

    // No zones, or the backend says shade rather than outline: frame the region.
    const bbox = result.region?.bbox
    if (bbox && bbox.length === 4) {
      map.fitBounds(
        [
          [bbox[1], bbox[0]],
          [bbox[3], bbox[2]],
        ],
        { padding: [20, 20] },
      )
    }
  }, [result, onSelectZone])

  // Clip the top layer to wherever the divider sits.
  //
  // Applied to the layer's own container rather than by redrawing tiles, so
  // dragging costs one style write per frame and Leaflet never refetches.
  useEffect(() => {
    const element = layersRef.current.overlay?.getContainer()
    if (!element) return
    element.style.clipPath = swipeEnabled ? clipInset(swipe) : ''
  }, [swipe, swipeEnabled, result])

  // Fly to a zone selected in the table.
  useEffect(() => {
    const map = mapRef.current
    if (!map || !selectedZone || !result?.zones) return
    const zone = result.zones.find((z) => z.id === selectedZone)
    if (!zone?.centroid) return
    map.flyTo([zone.centroid[1], zone.centroid[0]], 11, { duration: 0.8 })
  }, [selectedZone, result])

  // Drag a rectangle on the map.
  //
  // Leaflet has no built-in rectangle tool and leaflet-draw is a dependency
  // and a stylesheet for one gesture, so this is three map events. The
  // arithmetic - which corner is which, how big is it, is it over India - is
  // in lib/bbox.js where it can be tested, because a box dragged right-to-left
  // has its corners the wrong way round and the backend would reject it with a
  // message about coordinate order that helps nobody.
  useEffect(() => {
    const map = mapRef.current
    // Polygons are clicked, not dragged, and have their own effect below.
    if (!map || !window.L || !drawing || drawing === 'polygon') return

    const state = drawRef.current
    const isCircle = drawing === 'circle'
    // Panning and drawing are the same gesture, so one has to give way.
    map.dragging.disable()
    map.getContainer().style.cursor = 'crosshair'

    const style = {
      color: '#58a6ff',
      weight: 2,
      fillOpacity: 0.12,
      dashArray: '6 4',
    }

    const onDown = (event) => {
      state.active = true
      state.origin = event.latlng
      if (state.rectangle) {
        map.removeLayer(state.rectangle)
        state.rectangle = null
      }
    }

    const onMove = (event) => {
      if (!state.active || !state.origin) return

      if (isCircle) {
        // Drag outwards from the centre: the distance IS the radius.
        const radius = distanceKm(state.origin, event.latlng)
        if (state.rectangle) {
          state.rectangle.setRadius(radius * 1000)
        } else {
          state.rectangle = window.L
            .circle(state.origin, { ...style, radius: radius * 1000 })
            .addTo(map)
        }
        return
      }

      const bounds = [
        [state.origin.lat, state.origin.lng],
        [event.latlng.lat, event.latlng.lng],
      ]
      if (state.rectangle) {
        state.rectangle.setBounds(bounds)
      } else {
        state.rectangle = window.L.rectangle(bounds, style).addTo(map)
      }
    }

    const onUp = (event) => {
      if (!state.active || !state.origin) return
      state.active = false

      const centre = state.origin
      state.origin = null

      const area = isCircle
        ? { kind: 'circle', centre: { lat: centre.lat, lng: centre.lng },
            radiusKm: distanceKm(centre, event.latlng) }
        : { kind: 'bbox', bbox: normaliseBounds(centre, event.latlng) }

      const check = isCircle
        ? validateCircle(area.centre, area.radiusKm)
        : validate(area.bbox)

      if (!check.ok) {
        setDrawError(check.reason)
        if (state.rectangle) {
          map.removeLayer(state.rectangle)
          state.rectangle = null
        }
        return
      }

      setDrawError('')
      setDrawing(null)
      onDrawArea?.(area)
    }

    map.on('mousedown', onDown)
    map.on('mousemove', onMove)
    map.on('mouseup', onUp)

    return () => {
      map.off('mousedown', onDown)
      map.off('mousemove', onMove)
      map.off('mouseup', onUp)
      map.dragging.enable()
      const container = map.getContainer()
      if (container) container.style.cursor = ''
    }
  }, [drawing, onDrawArea])

  // Clear the half-drawn ring's preview layers off the map. Called from
  // several places - finishing, cancelling, unmounting - so it is one
  // function rather than three copies that can drift apart.
  const clearRingPreview = useCallback(() => {
    const map = mapRef.current
    const state = ringRef.current

    if (map) {
      if (state.outline) map.removeLayer(state.outline)
      state.vertices.forEach((marker) => map.removeLayer(marker))
    }
    state.outline = null
    state.vertices = []
    state.points = []
    setRing([])
  }, [])

  // Polygon drawing: click to place a corner, click the first corner again -
  // or press Enter - to close it.
  //
  // A different gesture from the box and circle, and it has to be. A rectangle
  // is two corners, so a drag expresses it exactly. An arbitrary outline is
  // not; dragging a freehand path would put hundreds of points in a ring
  // capped at 500 and hand Earth Engine a shape nobody can edit. Clicks give
  // the user one corner at a time and a Backspace to take it back.
  //
  // The shape is only validated on close, not on every click, because a ring
  // in progress is legitimately invalid - two points enclose nothing, and
  // saying so after each click would be noise rather than help.
  useEffect(() => {
    const map = mapRef.current
    if (!map || !window.L || drawing !== 'polygon') return

    const state = ringRef.current
    map.getContainer().style.cursor = 'crosshair'

    // Dragging stays enabled, unlike the box tool: placing corners around a
    // catchment usually means panning between them, and a polygon gesture is
    // clicks rather than a drag, so the two do not collide.
    const style = { color: '#58a6ff', weight: 2, fillOpacity: 0.12, dashArray: '6 4' }

    const redraw = (points) => {
      if (state.outline) map.removeLayer(state.outline)
      state.vertices.forEach((marker) => map.removeLayer(marker))
      state.vertices = []
      state.outline = null

      if (points.length >= 2) {
        const path = points.map((p) => [p.lat, p.lng])
        state.outline =
          points.length >= 3
            ? window.L.polygon(path, style).addTo(map)
            : window.L.polyline(path, style).addTo(map)
      }

      points.forEach((point, index) => {
        state.vertices.push(
          window.L
            .circleMarker([point.lat, point.lng], {
              // The first vertex is the close target, so it is drawn larger.
              // Without that the user has no idea what to aim at.
              radius: index === 0 ? 7 : 4,
              color: '#58a6ff',
              weight: 2,
              fillColor: index === 0 ? '#58a6ff' : '#0d1117',
              fillOpacity: index === 0 ? 0.5 : 1,
            })
            .addTo(map),
        )
      })
    }

    // The ref is the authoritative list, not the state.
    //
    // The obvious shape - reading the current points inside a setRing updater
    // - is wrong. An updater has to be pure, and React calls it twice under
    // StrictMode. Closing the ring from in there would fire onDrawArea twice
    // and run the whole analysis twice, which nothing on screen would reveal
    // beyond an Earth Engine bill. So the handler reads the ref, and state is
    // a mirror kept only for rendering the hint.
    const commit = (points) => {
      state.points = points
      redraw(points)
      setRing(points)
    }

    const finish = () => {
      const points = state.points
      const check = validatePolygon(points)
      if (!check.ok) {
        // Left on the map rather than cleared: a shape refused for crossing
        // itself is one Backspace from being valid, and wiping it would make
        // the user start over for a fixable mistake.
        setDrawError(check.reason)
        return
      }
      clearRingPreview()
      setDrawError('')
      setDrawing(null)
      onDrawArea?.({ kind: 'polygon', points })
    }

    const onClick = (event) => {
      const points = state.points

      // Closing: a click on the first vertex, judged in screen pixels so the
      // target is the same size whatever the zoom.
      if (points.length >= 3) {
        const first = map.latLngToContainerPoint([points[0].lat, points[0].lng])
        if (withinSnap(event.containerPoint, first)) {
          finish()
          return
        }
      }

      setDrawError('')
      commit([...points, { lat: event.latlng.lat, lng: event.latlng.lng }])
    }

    const onKey = (event) => {
      const action = actionFromKey(event.key)
      if (!action) return
      event.preventDefault()

      if (action === 'cancel') {
        clearRingPreview()
        setDrawError('')
        setDrawing(null)
        return
      }
      if (action === 'finish') {
        finish()
        return
      }

      setDrawError('')
      commit(state.points.slice(0, -1))       // undo
    }

    map.on('click', onClick)
    window.addEventListener('keydown', onKey)

    return () => {
      map.off('click', onClick)
      window.removeEventListener('keydown', onKey)
      const container = map.getContainer()
      if (container) container.style.cursor = ''
      // Leaving the tool by any route - finishing, cancelling, switching to
      // the box tool, unmounting - takes the half-drawn ring with it. Without
      // this a cancelled outline stays painted on the map with no way to
      // remove it, looking exactly like a committed area.
      clearRingPreview()
    }
  }, [drawing, onDrawArea, clearRingPreview])

  // Keep the drawn shape on screen once the drag is over, and take it away
  // when the area is cleared. Redrawn from scratch rather than mutated,
  // because a box and a circle are different Leaflet layer types and reusing
  // one as the other silently does nothing.
  useEffect(() => {
    const map = mapRef.current
    if (!map || !window.L) return

    const state = drawRef.current
    if (state.rectangle) {
      map.removeLayer(state.rectangle)
      state.rectangle = null
    }
    if (!drawnArea) return

    const style = { color: '#58a6ff', weight: 2, fillOpacity: 0.08 }

    if (drawnArea.kind === 'circle') {
      state.rectangle = window.L
        .circle(drawnArea.centre, { ...style, radius: drawnArea.radiusKm * 1000 })
        .addTo(map)
      return
    }

    if (drawnArea.kind === 'polygon') {
      state.rectangle = window.L
        .polygon(drawnArea.points.map((p) => [p.lat, p.lng]), style)
        .addTo(map)
      return
    }

    if (drawnArea.kind === 'boundary') {
      // An uploaded outline: drawn, and framed, before anything is run, so
      // a wrong shape is caught by eye before it costs an analysis.
      state.rectangle = window.L.polygon(toLatLngs(drawnArea.boundary), style).addTo(map)
      map.fitBounds(state.rectangle.getBounds(), { padding: [20, 20] })
      return
    }

    const [west, south, east, north] = drawnArea.bbox
    state.rectangle = window.L
      .rectangle([[south, west], [north, east]], style)
      .addTo(map)
  }, [drawnArea])

  const moveTo = useCallback((clientX) => {
    setSwipe(percentFromPointer(clientX, containerRef.current?.getBoundingClientRect()))
  }, [])

  const handlePointerDown = useCallback(
    (event) => {
      event.preventDefault()
      draggingRef.current = true
      // Leaflet would otherwise pan the map under the divider.
      mapRef.current?.dragging.disable()
      event.currentTarget.setPointerCapture?.(event.pointerId)
      moveTo(event.clientX)
    },
    [moveTo],
  )

  const handlePointerMove = useCallback(
    (event) => {
      if (!draggingRef.current) return
      moveTo(event.clientX)
    },
    [moveTo],
  )

  const endDrag = useCallback((event) => {
    if (!draggingRef.current) return
    draggingRef.current = false
    mapRef.current?.dragging.enable()
    event.currentTarget.releasePointerCapture?.(event.pointerId)
  }, [])

  const handleKeyDown = useCallback(
    (event) => {
      const next = stepFromKey(event.key, swipe, event.shiftKey)
      // Only claim the keys we handle, or Tab stops escaping the handle.
      if (next === null) return
      event.preventDefault()
      setSwipe(next)
    },
    [swipe],
  )

  const hints = result?.map

  return (
    <div className="map-wrap">
      <div ref={containerRef} className="map" />

      <div className="draw-bar">
        {drawnArea ? (
          <>
            <span className="draw-chip" title="The area measured, not a district">
              {drawnArea.kind === 'circle'
                ? describeCircle(drawnArea.centre, drawnArea.radiusKm)
                : drawnArea.kind === 'polygon'
                  ? describePolygon(drawnArea.points)
                  : drawnArea.kind === 'boundary'
                    ? describeBoundary(drawnArea)
                    : describeBbox(drawnArea.bbox)}
            </span>
            <button type="button" onClick={() => { setDrawError(''); onDrawArea?.(null) }}>
              Use a named region
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              className={drawing === 'box' ? 'drawing' : ''}
              onClick={() => {
                setDrawError('')
                setDrawing((mode) => (mode === 'box' ? null : 'box'))
              }}
            >
              {drawing === 'box' ? 'Drag a box, or cancel' : 'Draw a box'}
            </button>
            <button
              type="button"
              className={drawing === 'circle' ? 'drawing' : ''}
              onClick={() => {
                setDrawError('')
                setDrawing((mode) => (mode === 'circle' ? null : 'circle'))
              }}
            >
              {drawing === 'circle' ? 'Drag outwards, or cancel' : 'Draw a circle'}
            </button>
            <button
              type="button"
              className={drawing === 'polygon' ? 'drawing' : ''}
              onClick={() => {
                setDrawError('')
                clearRingPreview()
                setDrawing((mode) => (mode === 'polygon' ? null : 'polygon'))
              }}
            >
              {drawing === 'polygon' ? 'Click corners, or cancel' : 'Draw a shape'}
            </button>
            {onUploadBoundary && (
              <>
                <button
                  type="button"
                  title="GeoJSON, KML/KMZ or a zipped shapefile, in longitude/latitude"
                  onClick={() => fileRef.current?.click()}
                >
                  Upload boundary
                </button>
                <input
                  ref={fileRef} type="file" hidden accept={ACCEPTED.join(',')}
                  onChange={(event) => {
                    const file = event.target.files?.[0]
                    event.target.value = ''
                    if (file) onUploadBoundary(file)
                  }}
                />
              </>
            )}
          </>
        )}
      </div>

      {(boundaryChoices || boundaryMessage) && (
        <div className="boundary-picker">
          {boundaryMessage && <p className="boundary-message">{boundaryMessage}</p>}
          {boundaryChoices && (
            <>
              <p>
                <strong>{boundaryChoices.file.name}</strong> has {boundaryChoices.features.length} shapes.
                Pick the one to analyse:
              </p>
              <ul>
                {pickerItems(boundaryChoices).map((item) => (
                  <li key={item.index}>
                    <button type="button" disabled={item.disabled} onClick={() => onPickBoundary?.(item.index)}>
                      {item.label}
                    </button>
                    <small> {item.detail}</small>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}

      {(drawing || drawError) && (
        <div className={`draw-hint${drawError ? ' draw-hint-error' : ''}`}>
          {drawError ||
            (drawing === 'polygon'
              ? drawingHint(ring)
              : drawing === 'circle'
                ? 'Press at the centre and drag outwards. The distance you drag is the radius.'
                : 'Drag on the map to box an area. Works anywhere, including districts too new for the 2015 boundaries.')}
        </div>
      )}

      {swipeEnabled && (
        <div className="swipe">
          <span className="swipe-tag swipe-tag-before">
            Before · {periodLabel(result?.period?.pre)}
          </span>
          <span className="swipe-tag swipe-tag-after">
            After · {periodLabel(result?.period?.post)}
          </span>

          <div className="swipe-line" style={{ left: `${swipe}%` }}>
            <button
              type="button"
              className="swipe-handle"
              onPointerDown={handlePointerDown}
              onPointerMove={handlePointerMove}
              onPointerUp={endDrag}
              onPointerCancel={endDrag}
              onKeyDown={handleKeyDown}
              role="slider"
              aria-label="Reveal the before image"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(swipe)}
              aria-valuetext={`${Math.round(swipe)}% before`}
            >
              <span aria-hidden="true">⇤⇥</span>
            </button>
          </div>
        </div>
      )}

      {hints?.warning_banner && (
        <div className="map-warning">{hints.warning_banner}</div>
      )}

      {result && (
        <div className="map-legend">
          <span className="legend-title">
            {hints?.primary_layer === 'zone_polygons' ? 'Zones' : 'Coverage'}
          </span>
          {hints?.primary_layer === 'zone_polygons' ? (
            Object.entries(SEVERITY_COLOURS).map(([name, colour]) => (
              <span key={name} className="legend-item">
                <i style={{ background: colour }} /> {name}
              </span>
            ))
          ) : (
            <span className="legend-item">
              <i style={{ background: hints?.palette?.[0] || '#3182bd' }} /> detected
            </span>
          )}
          {result.artifacts?.terrain_excluded_tiles && (
            <span className="legend-item" title="Dark on radar, but too high above drainage or too steep to be flood">
              <i style={{ background: '#c51b8a' }} /> not counted (terrain)
            </span>
          )}
        </div>
      )}

      {!result && (
        <div className="map-empty">
          <p>Run an analysis to see it on the map.</p>
        </div>
      )}
    </div>
  )
}
