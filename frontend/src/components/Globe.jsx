import { useEffect, useRef, useState } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { feature } from 'topojson-client'
import land110 from 'world-atlas/land-110m.json'
import { eciToEcf, gstime, jday, sunPos } from 'satellite.js'

import { prefersReducedMotion } from '../hooks'
import { satrecFor, stateAt, track } from '../lib/orbit'

/**
 * The live globe: the real Earth (Natural Earth coastlines, public domain),
 * lit from where the Sun really is, with each active Sentinel-1 at the
 * position SGP4 gives for this second, its orbit for the next and last ~49
 * minutes, radar pulses towards the ground beneath it, and ESA's next planned
 * acquisitions drawn as their real footprints.
 *
 * The globe is Earth-fixed (it does not spin): satellites move along their
 * true ground tracks over it. Altitudes are drawn ALT_EXAGGERATION times
 * higher than scale, or 693 km would be a hair above the surface.
 *
 * No borders are drawn - India is marked by a glow, not a boundary line.
 */

const ALT_EXAGGERATION = 2.6
const EARTH_KM = 6371
const INDIA = { lat: 22.5, lon: 79.0 }
const TEAL = 0x7fd3d4
const AMBER = 0xffb020

/** lat/lon (degrees) on a sphere of radius r, matching three's sphere UVs. */
function toVector(lat, lon, r = 1) {
  const phi = (90 - lat) * (Math.PI / 180)
  const theta = (lon + 180) * (Math.PI / 180)
  return new THREE.Vector3(-r * Math.sin(phi) * Math.cos(theta), r * Math.cos(phi), r * Math.sin(phi) * Math.sin(theta))
}

const altRadius = (altKm) => 1 + (Math.max(0, altKm) / EARTH_KM) * ALT_EXAGGERATION

function earthTexture() {
  const W = 2048
  const H = 1024
  const canvas = document.createElement('canvas')
  canvas.width = W
  canvas.height = H
  const ctx = canvas.getContext('2d')
  const sea = ctx.createLinearGradient(0, 0, 0, H)
  sea.addColorStop(0, '#0B3A63')
  sea.addColorStop(0.5, '#0E4C7A')
  sea.addColorStop(1, '#0B3A63')
  ctx.fillStyle = sea
  ctx.fillRect(0, 0, W, H)

  const x = (lon) => ((lon + 180) / 360) * W
  const y = (lat) => ((90 - lat) / 180) * H
  const land = feature(land110, land110.objects.land)
  ctx.fillStyle = '#2E9E83'
  ctx.strokeStyle = '#7FD3D4'
  ctx.lineWidth = 1.2
  for (const f of land.features) {
    const polys = f.geometry.type === 'Polygon' ? [f.geometry.coordinates] : f.geometry.coordinates
    for (const poly of polys) {
      ctx.beginPath()
      for (const ring of poly) {
        ring.forEach(([lon, lat], i) => {
          const px = x(lon)
          const jump = i > 0 && Math.abs(px - x(ring[i - 1][0])) > W / 2
          if (i === 0 || jump) ctx.moveTo(px, y(lat))
          else ctx.lineTo(px, y(lat))
        })
        ctx.closePath()
      }
      ctx.fill('evenodd')
      ctx.stroke()
    }
  }

  // Graticule every 15 degrees.
  ctx.strokeStyle = 'rgba(255,255,255,0.07)'
  ctx.lineWidth = 1
  for (let lon = -180; lon <= 180; lon += 15) { ctx.beginPath(); ctx.moveTo(x(lon), 0); ctx.lineTo(x(lon), H); ctx.stroke() }
  for (let lat = -75; lat <= 75; lat += 15) { ctx.beginPath(); ctx.moveTo(0, y(lat)); ctx.lineTo(W, y(lat)); ctx.stroke() }

  // India: a soft glow, not a border.
  const gx = x(INDIA.lon)
  const gy = y(INDIA.lat)
  const glow = ctx.createRadialGradient(gx, gy, 4, gx, gy, 90)
  glow.addColorStop(0, 'rgba(255, 214, 120, 0.55)')
  glow.addColorStop(1, 'rgba(255, 214, 120, 0)')
  ctx.fillStyle = glow
  ctx.fillRect(gx - 100, gy - 100, 200, 200)

  const texture = new THREE.CanvasTexture(canvas)
  texture.colorSpace = THREE.SRGBColorSpace
  texture.anisotropy = 4
  return texture
}

function satelliteModel() {
  const group = new THREE.Group()
  const bus = new THREE.Mesh(new THREE.BoxGeometry(0.03, 0.022, 0.022),
    new THREE.MeshStandardMaterial({ color: 0xe8f1fb, metalness: 0.7, roughness: 0.3 }))
  group.add(bus)
  const panelMat = new THREE.MeshStandardMaterial({ color: 0x1d5fa8, metalness: 0.6, roughness: 0.25, emissive: 0x0a2a55 })
  for (const side of [-1, 1]) {
    const panel = new THREE.Mesh(new THREE.BoxGeometry(0.07, 0.002, 0.022), panelMat)
    panel.position.x = side * 0.052
    group.add(panel)
  }
  // The radar antenna: a long flat panel under the bus, as on Sentinel-1.
  const sar = new THREE.Mesh(new THREE.BoxGeometry(0.002, 0.012, 0.06),
    new THREE.MeshStandardMaterial({ color: 0xc9d6e8, metalness: 0.5, roughness: 0.4 }))
  sar.position.y = -0.016
  group.add(sar)
  return group
}

function lineThrough(points, color, opacity) {
  const geometry = new THREE.BufferGeometry().setFromPoints(points)
  return new THREE.Line(geometry, new THREE.LineBasicMaterial({ color, transparent: true, opacity }))
}

function sunDirection(date) {
  const { rsun } = sunPos(jday(date))
  const ecf = eciToEcf(rsun, gstime(date))
  // Earth-fixed x (0 N, 0 E), y (0 N, 90 E), z (north) -> this scene's axes.
  return new THREE.Vector3(ecf.x, ecf.z, -ecf.y).normalize()
}

/** True when the Earth (unit sphere at the origin) hides `point` from `camera`. */
function hidden(point, camera) {
  const origin = camera.position
  const dir = point.clone().sub(origin)
  const length = dir.length()
  dir.normalize()
  const b = origin.dot(dir)
  const c = origin.lengthSq() - 1
  const disc = b * b - c
  if (disc < 0) return false
  const t = -b - Math.sqrt(disc)
  return t > 0 && t < length
}

export default function Globe({ satellites = [], footprints = [], focus = INDIA, zoom = false, className = '' }) {
  const mountRef = useRef(null)
  const labelsRef = useRef(null)
  const sceneRef = useRef(null)
  const [failed, setFailed] = useState(false)

  // Build the scene once.
  useEffect(() => {
    const mount = mountRef.current
    if (!mount) return undefined
    let renderer
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
    } catch {
      setTimeout(() => setFailed(true), 0)
      return undefined
    }
    const reduced = prefersReducedMotion()
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.setSize(mount.clientWidth, mount.clientHeight)
    mount.appendChild(renderer.domElement)

    const scene = new THREE.Scene()
    const camera = new THREE.PerspectiveCamera(40, mount.clientWidth / Math.max(1, mount.clientHeight), 0.01, 100)
    camera.position.copy(toVector(focus.lat, focus.lon, 3.4))

    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true
    controls.enablePan = false
    controls.enableZoom = zoom
    controls.minDistance = 1.6
    controls.maxDistance = 6
    controls.rotateSpeed = 0.5
    controls.autoRotate = !reduced
    controls.autoRotateSpeed = 0.35
    controls.addEventListener('start', () => { controls.autoRotate = false })

    scene.add(new THREE.AmbientLight(0xbfd7ff, 0.75))
    const sun = new THREE.DirectionalLight(0xffffff, 2.2)
    scene.add(sun)

    const earth = new THREE.Mesh(new THREE.SphereGeometry(1, 96, 96),
      new THREE.MeshPhongMaterial({ map: earthTexture(), shininess: 12, specular: new THREE.Color(0x1d4f7a) }))
    scene.add(earth)
    const atmosphere = new THREE.Mesh(new THREE.SphereGeometry(1.06, 64, 64),
      new THREE.MeshBasicMaterial({ color: 0x39c5cf, transparent: true, opacity: 0.12, side: THREE.BackSide,
        blending: THREE.AdditiveBlending }))
    scene.add(atmosphere)

    // Stars.
    const starPositions = new Float32Array(900 * 3)
    for (let i = 0; i < starPositions.length; i++) starPositions[i] = (Math.random() - 0.5) * 60
    const starGeometry = new THREE.BufferGeometry()
    starGeometry.setAttribute('position', new THREE.BufferAttribute(starPositions, 3))
    scene.add(new THREE.Points(starGeometry, new THREE.PointsMaterial({ color: 0x9fd8ff, size: 0.05, transparent: true, opacity: 0.7 })))

    // India marker: a dot and a ring that keeps pinging.
    const indiaPoint = toVector(INDIA.lat, INDIA.lon, 1.002)
    const dot = new THREE.Mesh(new THREE.SphereGeometry(0.012, 16, 16), new THREE.MeshBasicMaterial({ color: 0xffd678 }))
    dot.position.copy(indiaPoint)
    scene.add(dot)
    const ping = new THREE.Mesh(new THREE.RingGeometry(0.02, 0.026, 48),
      new THREE.MeshBasicMaterial({ color: 0xffd678, transparent: true, opacity: 0.8, side: THREE.DoubleSide }))
    ping.position.copy(indiaPoint)
    ping.lookAt(indiaPoint.clone().multiplyScalar(2))
    scene.add(ping)

    const dynamic = new THREE.Group()
    scene.add(dynamic)

    sceneRef.current = { scene, camera, controls, renderer, dynamic, sats: [], footprints: null, reduced }

    let running = true
    let frame
    let lastTrack = 0
    let lastSun = 0
    const clock = new THREE.Clock()

    const update = () => {
      const now = new Date()
      const state = sceneRef.current
      if (now.getTime() - lastSun > 60000) {
        sun.position.copy(sunDirection(now).multiplyScalar(10))
        lastSun = now.getTime()
      }
      const refreshTrack = state.forceTrack || now.getTime() - lastTrack > 60000
      state.forceTrack = false
      for (const s of state.sats) {
        const st = stateAt(s.satrec, now)
        if (!st) continue
        const position = toVector(st.lat, st.lon, altRadius(st.altKm))
        s.model.position.copy(position)
        s.model.lookAt(0, 0, 0)
        s.position = position
        if (refreshTrack) {
          const pts = track(s.satrec, now, { minutesBack: 49, minutesAhead: 49, stepS: 45 })
            .map((p) => toVector(p.lat, p.lon, altRadius(p.altKm)))
          s.orbit.geometry.dispose()
          s.orbit.geometry = new THREE.BufferGeometry().setFromPoints(pts)
        }
        const ground = toVector(st.lat, st.lon, 1)
        s.pulses.forEach((pulse) => {
          pulse.phase = (pulse.phase + (state.reduced ? 0 : 0.008)) % 1
          pulse.mesh.position.lerpVectors(position, ground, pulse.phase)
          pulse.mesh.lookAt(0, 0, 0)
          const scale = 0.5 + pulse.phase * 2.4
          pulse.mesh.scale.set(scale, scale, scale)
          pulse.mesh.material.opacity = state.reduced ? 0 : (1 - pulse.phase) * 0.7
        })
      }
      if (refreshTrack) lastTrack = now.getTime()

      const t = clock.getElapsedTime()
      const k = state.reduced ? 0.5 : (t % 2) / 2
      ping.scale.setScalar(1 + k * 3)
      ping.material.opacity = 0.8 * (1 - k)
      if (state.footprints) state.footprints.material.opacity = 0.65 + 0.3 * Math.sin(t * 3)

      // Satellite labels, as HTML so they stay sharp.
      const labels = labelsRef.current
      if (labels) {
        const w = mount.clientWidth
        const h = mount.clientHeight
        state.sats.forEach((s, i) => {
          const el = labels.children[i]
          if (!el || !s.position) return
          const p = s.position.clone().project(camera)
          const visible = p.z < 1 && !hidden(s.position, camera)
          el.style.opacity = visible ? '1' : '0'
          el.style.transform = `translate(${((p.x + 1) / 2) * w + 10}px, ${((1 - p.y) / 2) * h - 10}px)`
        })
      }
    }

    const loop = () => {
      frame = requestAnimationFrame(loop)
      if (!running) return
      controls.update()
      update()
      renderer.render(scene, camera)
    }
    loop()

    const visibility = new IntersectionObserver(([entry]) => { running = entry.isIntersecting && !document.hidden })
    visibility.observe(mount)
    const onHidden = () => { running = !document.hidden }
    document.addEventListener('visibilitychange', onHidden)
    const resize = new ResizeObserver(() => {
      const w = mount.clientWidth
      const h = Math.max(1, mount.clientHeight)
      renderer.setSize(w, h)
      camera.aspect = w / h
      camera.updateProjectionMatrix()
    })
    resize.observe(mount)

    return () => {
      cancelAnimationFrame(frame)
      visibility.disconnect()
      resize.disconnect()
      document.removeEventListener('visibilitychange', onHidden)
      controls.dispose()
      scene.traverse((obj) => {
        obj.geometry?.dispose?.()
        if (obj.material) [].concat(obj.material).forEach((m) => { m.map?.dispose?.(); m.dispose?.() })
      })
      renderer.dispose()
      renderer.domElement.remove()
      sceneRef.current = null
    }
    // The scene is built once; satellites, footprints and focus update below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Satellites: rebuild their models when the list changes.
  const satKey = satellites.map((s) => `${s.key}:${s.epoch}`).join('|')
  useEffect(() => {
    const state = sceneRef.current
    if (!state) return
    state.sats.forEach((s) => {
      state.dynamic.remove(s.model, s.orbit)
      s.pulses.forEach((p) => state.dynamic.remove(p.mesh))
    })
    state.sats = satellites.map((sat) => {
      const model = satelliteModel()
      const orbit = lineThrough([new THREE.Vector3(), new THREE.Vector3()], TEAL, 0.55)
      const pulses = [0, 0.33, 0.66].map((phase) => {
        const mesh = new THREE.Mesh(new THREE.RingGeometry(0.018, 0.022, 40, 1, 0, Math.PI * 1.1),
          new THREE.MeshBasicMaterial({ color: TEAL, transparent: true, opacity: 0.6, side: THREE.DoubleSide }))
        state.dynamic.add(mesh)
        return { mesh, phase }
      })
      state.dynamic.add(model, orbit)
      return { key: sat.key, satrec: satrecFor(sat), model, orbit, pulses, position: null }
    }).filter((s) => s.satrec)
    // Draw the orbit lines on the next frame rather than in a minute.
    state.forceTrack = true
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [satKey])

  // ESA's planned footprints, as glowing outlines on the ground.
  const footKey = footprints.map((f) => `${f.satellite}${f.start}`).join('|')
  useEffect(() => {
    const state = sceneRef.current
    if (!state) return
    if (state.footprints) {
      state.dynamic.remove(state.footprints)
      state.footprints.geometry.dispose()
    }
    // Each edge is cut into short pieces: a footprint is hundreds of km long,
    // and a straight chord between its corners would run under the surface.
    const points = []
    for (const take of footprints) {
      const ring = take.footprint || []
      for (let i = 1; i < ring.length; i++) {
        const [lon0, lat0] = ring[i - 1]
        const [lon1, lat1] = ring[i]
        const steps = Math.max(1, Math.ceil(Math.hypot(lon1 - lon0, lat1 - lat0) / 0.5))
        for (let k = 0; k < steps; k++) {
          const a = k / steps
          const b = (k + 1) / steps
          points.push(toVector(lat0 + (lat1 - lat0) * a, lon0 + (lon1 - lon0) * a, 1.004),
            toVector(lat0 + (lat1 - lat0) * b, lon0 + (lon1 - lon0) * b, 1.004))
        }
      }
    }
    if (!points.length) {
      state.footprints = null
      return
    }
    state.footprints = new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(points),
      new THREE.LineBasicMaterial({ color: AMBER, transparent: true, opacity: 0.9 }))
    state.dynamic.add(state.footprints)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [footKey])

  // Turn to face a place when asked (a searched district, say).
  useEffect(() => {
    const state = sceneRef.current
    if (!state || !focus) return
    const distance = state.camera.position.length()
    state.camera.position.copy(toVector(focus.lat, focus.lon, distance))
    state.controls.autoRotate = false
    state.controls.update()
  }, [focus?.lat, focus?.lon]) // eslint-disable-line react-hooks/exhaustive-deps

  if (failed) {
    return (
      <div className={`globe globe-failed ${className}`}>
        <p>This browser cannot draw the 3D globe (WebGL is off). The figures beside it are unaffected.</p>
      </div>
    )
  }

  return (
    <div className={`globe ${className}`}>
      <div ref={mountRef} className="globe-canvas" />
      <div ref={labelsRef} className="globe-labels" aria-hidden="true">
        {satellites.map((s) => <span key={s.key} className="globe-label">{s.name}</span>)}
      </div>
    </div>
  )
}
