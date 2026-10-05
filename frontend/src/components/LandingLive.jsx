import { Suspense, lazy, useEffect, useMemo, useState } from 'react'

import { api } from '../api'
import { useNow } from '../hooks'
import { countdown, istTime, placeLabel, satrecFor, stateAt } from '../lib/orbit'
import { hrefFor } from '../lib/router'
import { namedAreas } from '../lib/world'
import Icon from './Icon'

const Globe = lazy(() => import('./Globe'))

/**
 * The landing page's live section: the same globe and figures as the
 * Satellites page, for anyone - the data is public. Loaded only when the
 * visitor scrolls near it, so the first screen stays fast.
 */
export default function LandingLive() {
  const now = useNow(1000)
  const [data, setData] = useState(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    api.satellites().then(setData).catch(() => setFailed(true))
  }, [])

  const satellites = useMemo(() => data?.satellites || [], [data])
  const satrecs = useMemo(() => satellites.map((sat) => ({ sat, satrec: satrecFor(sat) })).filter((s) => s.satrec), [satellites])
  const areas = useMemo(() => namedAreas(data?.india?.area?.geometry), [data])
  const next = data?.india?.next_planned?.[0]
  const last = data?.india?.last_image

  return (
    <div className="live-inner">
      <div className="live-globe">
        <Suspense fallback={<div className="globe globe-loading"><span className="spinner" /></div>}>
          <Globe satellites={satellites} footprints={data?.india?.next_planned || []} />
        </Suspense>
      </div>
      <div className="live-copy">
        <span className="eyebrow light"><span className="live-dot" /> Live from orbit</span>
        <h2>The satellites behind every map, right now</h2>
        <p>
          Real positions, worked out every second from published orbits, and ESA's own plan for when India
          is imaged next. No simulated numbers.
        </p>
        <ul className="live-stats">
          {satrecs.map(({ sat, satrec }) => {
            const st = stateAt(satrec, now)
            return st && (
              <li key={sat.key}>
                <span className="live-dot" />
                <div>
                  <strong>{sat.name}</strong>
                  <span>over {placeLabel(st.lat, st.lon, areas)} · {Math.round(st.altKm)} km up · {st.speedKmS.toFixed(1)} km/s</span>
                </div>
              </li>
            )
          })}
          {next && (
            <li>
              <Icon name="radar" size={18} />
              <div>
                <strong>Next planned image of India: {next.in_progress ? 'recording now' : countdown(new Date(next.start) - now)}</strong>
                <span>{istTime(next.start)} · {next.satellite.replace('S1', 'Sentinel-1')} · ESA acquisition plan</span>
              </div>
            </li>
          )}
          {last?.time && (
            <li>
              <Icon name="satellite" size={18} />
              <div>
                <strong>Newest image ready to analyse: {countdown(new Date(last.time) - now)}</strong>
                <span>{istTime(last.time)} · {last.platform} · Google Earth Engine</span>
              </div>
            </li>
          )}
          {failed && <li><span>The live feed is not reachable right now.</span></li>}
        </ul>
        <a className="btn btn-primary" href={hrefFor('signup')}>Track your district <Icon name="arrow" size={16} /></a>
      </div>
    </div>
  )
}
