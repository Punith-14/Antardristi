import { useMemo, useState } from 'react'

import {
  chartGeometry,
  monthLabel,
  prefersPercent,
  summaryLine,
} from '../lib/series'

const WIDTH = 320
const HEIGHT = 170

/**
 * A monthly flood series: one bar per month, each a complete analysis.
 *
 * Deliberately a bar per month and not a line. See lib/series.js for why a
 * missing month is an outlined empty slot rather than a zero, and why the
 * axis always starts at zero.
 *
 * Clicking a bar opens that month's own analysis - its evidence record,
 * zones and PDF - because the series adds no numbers of its own.
 */
export default function SeriesPanel({ series, onOpenMonth, opening }) {
  // Start on percent when some months were only partly seen: their km²
  // figures cover less ground, so the percentage is the fairer comparison.
  const [usePercent, setUsePercent] = useState(() => prefersPercent(series))
  const [hover, setHover] = useState(null)

  const geometry = useMemo(
    () => chartGeometry(series?.points, { width: WIDTH, height: HEIGHT, usePercent }),
    [series, usePercent],
  )

  if (!series?.points?.length) return null

  const comparability = series.comparability || {}
  const shown = hover != null ? geometry.bars[hover] : null

  return (
    <div className="panel result-panel series-panel">
      <div className="panel-head">
        <h2>Flood extent by month · {series.region?.name || 'drawn area'}</h2>
        <p>{summaryLine(series)} · {series.sensor}</p>
      </div>

      <div className="series-toggle" role="group" aria-label="Units">
        <button
          type="button"
          className={!usePercent ? 'active' : ''}
          aria-pressed={!usePercent}
          onClick={() => setUsePercent(false)}
        >
          km²
        </button>
        <button
          type="button"
          className={usePercent ? 'active' : ''}
          aria-pressed={usePercent}
          onClick={() => setUsePercent(true)}
        >
          % of observed area
        </button>
      </div>

      <svg
        className="series-chart"
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="img"
        aria-label={`Flood extent by month, ${summaryLine(series)}`}
      >
        <defs>
          <pattern id="series-hatch" width="5" height="5" patternUnits="userSpaceOnUse"
            patternTransform="rotate(45)">
            <line x1="0" y1="0" x2="0" y2="5" className="series-hatch-line" />
          </pattern>
        </defs>

        {geometry.ticks.map((tick) => (
          <g key={tick.value}>
            <line x1={geometry.padLeft} x2={WIDTH - 6} y1={tick.y} y2={tick.y}
              className="series-grid" />
            <text x={geometry.padLeft - 4} y={tick.y + 3} className="series-tick"
              textAnchor="end">
              {tick.value.toLocaleString()}
            </text>
          </g>
        ))}

        {geometry.bars.map((bar, index) => {
          const clickable = Boolean(bar.requestId)
          return (
            <g
              key={bar.label || index}
              className={`series-bar tone-${bar.tone}${clickable ? ' clickable' : ''}`}
              onMouseEnter={() => setHover(index)}
              onMouseLeave={() => setHover(null)}
              onClick={() => clickable && onOpenMonth?.(bar.requestId)}
              role={clickable ? 'button' : undefined}
              tabIndex={clickable ? 0 : undefined}
              aria-label={
                bar.value == null
                  ? `${bar.label}: not observed`
                  : `${bar.label}: ${bar.value} ${geometry.unit}. Open this month.`
              }
              onKeyDown={(event) => {
                if (clickable && (event.key === 'Enter' || event.key === ' ')) {
                  event.preventDefault()
                  onOpenMonth?.(bar.requestId)
                }
              }}
            >
              <rect x={bar.x} y={bar.y} width={bar.width} height={Math.max(bar.height, 0)} />
              {bar.tone === 'partial' && (
                <rect x={bar.x} y={bar.y} width={bar.width} height={bar.height}
                  fill="url(#series-hatch)" />
              )}
              {bar.tone === 'gap' && (
                <text x={bar.x + bar.width / 2} y={geometry.baseline - 6}
                  className="series-gap-label" textAnchor="middle">
                  no data
                </text>
              )}
              <text x={bar.x + bar.width / 2} y={HEIGHT - 8} className="series-month"
                textAnchor="middle">
                {monthLabel(bar.label)}
              </text>
            </g>
          )
        })}
      </svg>

      <div className="series-readout">
        {shown ? (
          <>
            <strong>{shown.label}</strong>{' '}
            {shown.value == null ? 'not observed' : `${shown.value.toLocaleString()} ${geometry.unit}`}
            {shown.point?.flags?.map((flag) => (
              <p key={flag.kind} className={`series-flag flag-${flag.kind}`}>{flag.text}</p>
            ))}
          </>
        ) : (
          <span>
            {opening ? 'Opening that month…' : 'Hover a month for detail; click to open its full analysis.'}
          </span>
        )}
      </div>

      <ul className="series-legend">
        <li><span className="swatch tone-ok" /> observed</li>
        <li><span className="swatch tone-partial" /> partly observed</li>
        <li><span className="swatch tone-different" /> measured differently</li>
        <li><span className="swatch tone-gap" /> not observed — a gap, not zero</li>
      </ul>

      {comparability.notes?.length > 0 && (
        <section className="series-notes">
          {comparability.notes.map((note) => <p key={note}>{note}</p>)}
        </section>
      )}
    </div>
  )
}
