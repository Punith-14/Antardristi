import { Suspense, lazy, useEffect, useMemo, useState } from 'react'

import { api } from '../api'
import { navigate, useNow } from '../hooks'
import {
  areaCentre, countdown, direction, istTime, latLonText, nextPass, placeLabel, satrecFor, stateAt,
} from '../lib/orbit'
import { hrefFor } from '../lib/router'
import { namedAreas } from '../lib/world'
import Icon from './Icon'
import { LastImage, PlannedList, Sources } from './SatelliteBits'

const Globe = lazy(() => import('./Globe'))

const INDIA_FOCUS = { lat: 22.5, lon: 79 }

/** One satellite, live: where it is, how high and fast, and its next pass. */
function LiveSatellite({ sat, satrec, now, areas, pass, passLabel }) {
  const st = stateAt(satrec, now)
  if (!st) return null
  return (
    <div className="card live-sat">
      <div className="live-sat-head">
        <span className="live-dot" />
        <h3>{sat.name}</h3>
        <span className="muted small">NORAD {sat.norad}</span>
      </div>
      <p className="live-where">Over {placeLabel(st.lat, st.lon, areas)}</p>
      <dl className="live-figures">
        <div><dt>Position</dt><dd>{latLonText(st.lat, st.lon)}</dd></div>
        <div><dt>Altitude</dt><dd>{Math.round(st.altKm)} km</dd></div>
        <div><dt>Speed</dt><dd>{st.speedKmS?.toFixed(2)} km/s</dd></div>
        <div><dt>Heading</dt><dd>{direction(satrec, now)}</dd></div>
      </dl>
      <p className="live-pass">
        <Icon name="clock" size={15} />
        {pass
          ? pass.inProgress
            ? <>Passing {passLabel} now</>
            : <>Next pass {passLabel}: <strong>{countdown(pass.start - now)}</strong> <span className="muted">({istTime(pass.start)})</span></>
          : <>No pass {passLabel} in the next 3 days</>}
      </p>
    </div>
  )
}

/** Passes over an area for every satellite, worked out once a minute. */
function usePasses(satrecs, geometry, now) {
  const minute = Math.floor(now.getTime() / 60000)
  return useMemo(() => {
    const from = new Date(minute * 60000)
    return satrecs.map(({ satrec }) => nextPass(satrec, geometry, from))
  }, [satrecs, geometry, minute])
}

export default function SatellitesPage({ mayRun }) {
  const now = useNow(1000)
  const [summary, setSummary] = useState(null)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [area, setArea] = useState(null)
  const [areaError, setAreaError] = useState('')
  const [areaBusy, setAreaBusy] = useState(false)

  useEffect(() => {
    let alive = true
    const load = () => api.satellites()
      .then((body) => { if (alive) { setSummary(body); setError('') } })
      .catch((err) => { if (alive) setError(err.message) })
    load()
    const id = setInterval(load, 10 * 60000)
    return () => { alive = false; clearInterval(id) }
  }, [])

  const satellites = useMemo(() => summary?.satellites || [], [summary])
  const satrecs = useMemo(() => satellites.map((sat) => ({ sat, satrec: satrecFor(sat) })).filter((s) => s.satrec), [satellites])
  const india = summary?.india
  const areas = useMemo(() => namedAreas(india?.area?.geometry), [india])
  const indiaPasses = usePasses(satrecs, india?.area?.geometry, now)
  const areaPasses = usePasses(satrecs, area?.area?.geometry, now)

  const lookUp = async (event) => {
    event.preventDefault()
    const name = query.trim()
    if (!name) return
    setAreaBusy(true)
    setAreaError('')
    try {
      setArea(await api.satelliteArea(name))
    } catch (err) {
      setArea(null)
      setAreaError(err.message)
    } finally {
      setAreaBusy(false)
    }
  }

  const focus = area?.area?.geometry ? areaCentre(area.area.geometry) : INDIA_FOCUS
  const footprints = (area ? area.next_planned : india?.next_planned) || []

  return (
    <main className="page sat-page">
      <div className="page-head">
        <div>
          <h1>Satellites</h1>
          <p>
            The radar satellites behind every flood map: where they are this second, when ESA plans them
            to image India next, and the newest image already available to analyse.
          </p>
        </div>
        <div className="live-clock" aria-live="off">
          <span className="live-dot" /> Live · {istTime(now)}
        </div>
      </div>

      {error && <p className="error">{error}</p>}

      <div className="sat-grid">
        <section className="card globe-card">
          <Suspense fallback={<div className="globe globe-loading"><span className="spinner dark" /> Loading the globe…</div>}>
            <Globe satellites={satellites} footprints={footprints} focus={focus} zoom />
          </Suspense>
          <div className="globe-legend">
            <span><i className="lg-sat" /> Satellite (height drawn 2.6× scale)</span>
            <span><i className="lg-orbit" /> Orbit, ±49 minutes</span>
            <span><i className="lg-plan" /> ESA's next planned images{area ? ` over ${area.area.name}` : ' over India'}</span>
            <span><i className="lg-india" /> India</span>
          </div>
        </section>

        <aside className="sat-side">
          {!summary && !error && [0, 1].map((i) => <div key={i} className="card live-sat skeleton"><i className="sk sk-line" /><i className="sk sk-line short" /><i className="sk sk-line" /></div>)}
          {satrecs.map(({ sat, satrec }, i) => (
            <LiveSatellite key={sat.key} sat={sat} satrec={satrec} now={now} areas={areas}
              pass={indiaPasses[i]} passLabel="over India" />
          ))}
          {summary && !satrecs.length && (
            <div className="card"><p className="sat-empty">Orbital elements are not available right now. {summary.sources?.elements?.error}</p></div>
          )}

          {india && (
            <div className="card india-card">
              <h3><Icon name="map" size={18} /> India</h3>
              <h4>Next planned images <small>ESA plan</small></h4>
              <PlannedList takes={india.next_planned.slice(0, 4)} now={now} />
              <h4>Last image available to analyse <small>Earth Engine</small></h4>
              <LastImage info={india.last_image} now={now} />
              {india.area?.approximate && <p className="sat-note">India's outline is approximate right now (Earth Engine could not be reached).</p>}
            </div>
          )}
        </aside>
      </div>

      <section className="card area-card">
        <div className="area-head">
          <div>
            <h2>Your area</h2>
            <p className="muted">Any state or district: when it is planned to be imaged next, and its newest image.</p>
          </div>
          <form className="area-form" onSubmit={lookUp}>
            <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Morigaon, Darbhanga, Kerala…"
              aria-label="State or district" />
            <button className="btn btn-primary" type="submit" disabled={areaBusy || !query.trim()}>
              {areaBusy && <span className="spinner" />}{areaBusy ? 'Looking…' : 'Check'}
            </button>
          </form>
        </div>
        {areaError && <p className="error">{areaError}</p>}
        {area && (
          <div className="area-grid">
            <div>
              <h4>{area.area.name}{area.area.state && area.area.state !== area.area.name ? `, ${area.area.state}` : ''} · next planned images</h4>
              <PlannedList takes={area.next_planned} now={now} />
            </div>
            <div>
              <h4>Next passes</h4>
              <ul className="pass-list">
                {satrecs.map(({ sat }, i) => (
                  <li key={sat.key}>
                    <span className="sat-chip">{sat.name}</span>
                    {areaPasses[i]
                      ? areaPasses[i].inProgress ? 'passing now' : <>{countdown(areaPasses[i].start - now)} <span className="muted">({istTime(areaPasses[i].start)})</span></>
                      : 'none in 3 days'}
                  </li>
                ))}
              </ul>
              <h4>Last image available to analyse</h4>
              <LastImage info={area.last_image} now={now} />
              {mayRun && (
                <button type="button" className="btn btn-primary area-go"
                  onClick={() => navigate(hrefFor('new', { region: area.area.name }))}>
                  Analyse {area.area.name} <Icon name="arrow" size={16} />
                </button>
              )}
            </div>
          </div>
        )}
      </section>

      <section className="sat-explain">
        <div className="card">
          <h3>A pass is not an image</h3>
          <p>
            A <strong>pass</strong> is when a satellite flies within reach of a place (its radar looks up to about
            300 km to the side). It only records when ESA has <strong>planned</strong> it to, so "next planned image"
            is the one to watch. Plans can still change, for example for emergency requests.
          </p>
        </div>
        <div className="card">
          <h3>Why "available to analyse" lags</h3>
          <p>
            An image reaches Earth Engine a few hours to about a day after it is taken. The last image shown is the
            newest one Antardrishti can analyse right now, with its scene ID so it can be checked.
          </p>
        </div>
        <div className="card">
          <h3>Where the numbers come from</h3>
          <Sources sources={summary?.sources} />
        </div>
      </section>
    </main>
  )
}
