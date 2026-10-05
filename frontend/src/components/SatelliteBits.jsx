import { useState } from 'react'

import { countdown, istTime } from '../lib/when'
import Icon from './Icon'

/** ESA's planned acquisitions as a list, soonest first. */
export function PlannedList({ takes, now, empty = 'No planned image over this area in the published plan.' }) {
  if (!takes?.length) return <p className="sat-empty">{empty}</p>
  return (
    <ol className="planned-list">
      {takes.map((t) => {
        const start = new Date(t.start)
        return (
          <li key={`${t.satellite}-${t.start}`} className={t.in_progress ? 'is-live' : ''}>
            <div className="planned-when">
              <strong>{istTime(t.start)}</strong>
              <span>{t.in_progress ? 'Recording now' : countdown(start - now)}</span>
            </div>
            <div className="planned-what">
              <span className="sat-chip">{t.satellite.replace('S1', 'Sentinel-1')}</span>
              <span title={t.mode_label}>{t.mode}</span>
              <span>{t.polarisation}</span>
              {t.orbit_relative != null && <span title="Relative orbit (track)">track {t.orbit_relative}</span>}
            </div>
          </li>
        )
      })}
    </ol>
  )
}

/** The newest image already in Earth Engine. */
export function LastImage({ info, now }) {
  const [copied, setCopied] = useState(false)
  if (!info) return <p className="sat-empty">Asking Earth Engine…</p>
  if (info.error) return <p className="sat-empty">{info.error}</p>
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(info.id)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      setCopied(false)
    }
  }
  return (
    <div className="last-image">
      <strong>{istTime(info.time)}</strong>
      <span className="muted">{countdown(new Date(info.time) - now)} · {info.platform}{info.pass ? ` · ${info.pass}` : ''}</span>
      {info.id && (
        <button type="button" className="scene-id-btn" onClick={copy} title="Copy the scene ID">
          <Icon name="satellite" size={14} /> {copied ? 'Copied' : info.id}
        </button>
      )}
    </div>
  )
}

/** Where each figure came from, and how fresh it is. */
export function Sources({ sources, note }) {
  if (!sources) return null
  const { elements, plans } = sources
  return (
    <div className="sat-sources">
      {note && <p>{note}</p>}
      <ul>
        {elements && (
          <li>
            Orbits: <a href={elements.source} target="_blank" rel="noreferrer">CelesTrak</a> elements
            {elements.fetched_at ? `, fetched ${istTime(elements.fetched_at)}` : ''}
            {elements.stale && <em> - {elements.error || 'using the last good copy'}</em>}
          </li>
        )}
        {plans && (
          <li>
            Planned images: <a href={plans.source} target="_blank" rel="noreferrer">ESA acquisition plans</a>
            {plans.files?.length ? ` (${plans.files.map((f) => f.satellite).join(', ')})` : ''}
            {plans.error && <em> - {plans.error}</em>}
          </li>
        )}
        <li>Last image: Google Earth Engine, collection COPERNICUS/S1_GRD.</li>
      </ul>
    </div>
  )
}
