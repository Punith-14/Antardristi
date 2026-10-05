import { useEffect, useState } from 'react'

import { prefersReducedMotion, useCountTo } from '../hooks'

/**
 * The landing page's moving picture: an illustrated river plain, before and
 * during a flood, with a slider to wipe between them and a radar sweep over
 * the top. It is an illustration, labelled as one - the real maps are inside.
 */
export default function HeroDemo() {
  const [split, setSplit] = useState(() => (prefersReducedMotion() ? 58 : 12))
  const [touched, setTouched] = useState(false)
  const area = useCountTo(684, { ms: 1800, start: true })
  const people = useCountTo(2.1, { ms: 1800, decimals: 1, start: true })

  // Glide the divider across once, so the flood "arrives" on first view.
  useEffect(() => {
    if (touched || prefersReducedMotion()) return undefined
    let frame
    const t0 = performance.now()
    const tick = (now) => {
      const t = Math.min(1, (now - t0 - 500) / 1600)
      if (t > 0) setSplit(12 + 46 * (1 - Math.pow(1 - t, 3)))
      if (t < 1) frame = requestAnimationFrame(tick)
    }
    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
  }, [touched])

  const x = (split / 100) * 560

  return (
    <div className="hero-demo">
      <svg viewBox="0 0 560 380" role="img" aria-label="Illustration: a river plain before and during a flood">
        <defs>
          <clipPath id="after-clip"><rect x={x} y="0" width={560 - x} height="380" /></clipPath>
          <linearGradient id="sweep" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0" stopColor="#7FD3D4" stopOpacity="0" />
            <stop offset="1" stopColor="#7FD3D4" stopOpacity="0.35" />
          </linearGradient>
        </defs>

        {/* Before: the normal river */}
        <rect width="560" height="380" fill="#1B3B66" />
        <g opacity="0.55" stroke="#2A5285" strokeWidth="1">
          {Array.from({ length: 9 }, (_, i) => <path key={`h${i}`} d={`M0 ${40 * i + 20} H560`} />)}
          {Array.from({ length: 14 }, (_, i) => <path key={`v${i}`} d={`M${40 * i + 20} 0 V380`} />)}
        </g>
        <path d="M70 40 L250 30 L330 80 L310 150 L180 160 L90 120 Z" fill="#20476F" />
        <path d="M330 250 L500 230 L530 330 L380 350 Z" fill="#20476F" />
        <path className="river" d="M-10 230 C80 190 150 260 250 222 S400 150 570 190" stroke="#3D6B9A" strokeWidth="18" fill="none" />

        {/* After: the same plain, flooded */}
        <g clipPath="url(#after-clip)">
          <rect width="560" height="380" fill="#173659" />
          <path d="M-10 230 C80 190 150 260 250 222 S400 150 570 190" stroke="#2C4250" strokeWidth="18" fill="none" />
          <g className="flood-blobs">
            <path d="M150 236 C190 196 260 206 292 232 C312 260 262 286 220 280 C178 274 136 262 150 236Z" />
            <path d="M330 186 C372 154 434 162 454 190 C464 216 412 232 376 222 C344 214 318 204 330 186Z" />
            <path d="M52 214 C72 198 104 202 110 220 C106 236 78 240 60 232Z" />
            <path d="M470 214 C488 204 516 206 520 222 C514 236 488 238 474 230Z" />
          </g>
        </g>

        {/* Area of interest */}
        <path className="aoi" d="M110 110 L360 90 L480 150 L470 300 L270 330 L100 290 Z" fill="none" stroke="#7FD3D4" strokeWidth="2" strokeDasharray="7 6" />

        {/* Radar sweep */}
        <g className="radar" style={{ transformOrigin: '470px 70px' }}>
          <path d="M470 70 L470 -130 A200 200 0 0 1 643 -30 Z" fill="url(#sweep)" />
        </g>
        <circle cx="470" cy="70" r="5" fill="#7FD3D4" />
        <circle className="ping" cx="470" cy="70" r="5" fill="none" stroke="#7FD3D4" strokeWidth="2" />

        {/* Divider */}
        <line x1={x} y1="0" x2={x} y2="380" stroke="#fff" strokeWidth="2" />
        <text x={Math.max(8, x - 70)} y="368" className="demo-tag">Before</text>
        <text x={Math.min(500, x + 10)} y="368" className="demo-tag">During</text>
      </svg>

      <input
        className="hero-slider" type="range" min="0" max="100" step="1" value={Math.round(split)}
        aria-label="Wipe between before and during the flood"
        onChange={(e) => { setTouched(true); setSplit(Number(e.target.value)) }}
      />

      <div className="float-card float-a">
        <span>Flooded area</span>
        <strong>{area} km²</strong>
      </div>
      <div className="float-card float-b">
        <span>People living there</span>
        <strong>{people} lakh</strong>
      </div>
      <p className="demo-caption">Illustration. Drag the slider to see before and during.</p>
    </div>
  )
}
