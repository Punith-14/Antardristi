import { useMemo, useState } from 'react'

import { assetUrl, citationSegments, pdfUrl } from '../api'
import {
  alignmentView,
  hasCoverage,
  isUpload,
  periodLine,
  sourceLine,
  writtenBy,
} from '../lib/ask'
import { SORTS, nextSort, sortZones } from '../lib/zonesort'

const fmt = (value, unit) => {
  if (value == null) return '—'
  if (unit === 'km2') return `${value.toLocaleString(undefined, { maximumFractionDigits: 1 })} km²`
  if (unit === 'percent') return `${value}%`
  if (unit === 'dB') return `${value} dB`
  return `${value.toLocaleString()} ${unit || ''}`.trim()
}

function Report({ report, verification, onCite }) {
  if (!report?.text) return null

  return (
    <section className="report">
      <div className="report-head">
        <h3>Finding</h3>
        <span className={`badge ${verification?.passed ? 'ok' : 'warn'}`}>
          {verification?.passed ? 'Verified' : 'Failed verification'}
        </span>
      </div>

      <p className="report-text">
        {citationSegments(report.text).map((segment) =>
          segment.kind === 'text' ? (
            <span key={segment.key}>{segment.value}</span>
          ) : (
            <span key={segment.key} className="cites">
              {segment.ids.map((id) => (
                <button key={id} className="cite" onClick={() => onCite(id)}>
                  {id}
                </button>
              ))}
            </span>
          ),
        )}
      </p>

      <div className="report-meta">
        <span>{writtenBy(report)}</span>
        {verification && (
          <>
            <span>
              {verification.claims_supported}/{verification.claims_total} claims traced
            </span>
            <span>
              {Math.round((verification.caveats?.completeness ?? 1) * 100)}% of caveats kept
            </span>
          </>
        )}
      </div>

      {verification?.unsupported_claims?.length > 0 && (
        <div className="unsupported">
          <strong>Unsupported figures flagged:</strong>{' '}
          {verification.unsupported_claims.map((c) => c.claim).join(', ')}
        </div>
      )}
    </section>
  )
}

function Coverage({ observation, unobserved }) {
  if (!observation) return null
  // No coverage figure means no bar. This used to default to 1, which printed
  // "100.0% of the region observed" for an uploaded photo with no location.
  const measured = hasCoverage(observation)
  const fraction = measured ? observation.coverage_fraction : null
  const partial = measured && fraction < 0.9

  return (
    <section className={`coverage ${partial ? 'partial' : ''}`}>
      {measured && (
        <div className="coverage-bar">
          <div style={{ width: `${Math.min(fraction * 100, 100)}%` }} />
        </div>
      )}
      <div className="coverage-meta">
        {measured && <strong>{(fraction * 100).toFixed(1)}% of the region observed</strong>}
        <span>{sourceLine(observation)}</span>
      </div>
      {unobserved?.notes?.length > 0 && (
        <ul className="notes">
          {unobserved.notes.map((note, index) => (
            <li key={index}>{note}</li>
          ))}
        </ul>
      )}
    </section>
  )
}

/**
 * What the question was taken to mean, and whether the analysis answers it.
 *
 * Placed ABOVE the finding. A report can be perfectly faithful to its numbers
 * while answering a different question - May against May passed 16 of 16
 * number checks - so if this fails it has to be read first, not found later.
 */
function Understood({ result }) {
  const view = alignmentView(result.alignment)
  if (!result.question && !view) return null

  return (
    <section className={`understood ${view ? `understood-${view.tone}` : ''}`}>
      {result.question && (
        <p className="understood-question">
          <span>You asked</span> {result.question}
        </p>
      )}
      {result.understood && (
        <p className="understood-line">
          <span>Understood as</span> {result.understood}
        </p>
      )}
      {view && (
        <>
          <p className="understood-headline">{view.headline}</p>
          {view.failures.length > 0 && (
            <ul>
              {view.failures.map((failure) => <li key={failure}>{failure}</li>)}
            </ul>
          )}
        </>
      )}
    </section>
  )
}

/**
 * The uploaded image with the counted pixels tinted, so what was measured
 * can be seen - drawn from the same mask the number came from.
 */
function UploadPreview({ result }) {
  const src = assetUrl(result.artifacts?.overlay_image)
  if (!src) return null
  return (
    <section className="upload-preview">
      <img src={src} alt="The uploaded image with water-coloured pixels tinted magenta" />
      <small>
        Tinted pixels are the ones counted. Magenta is used because it is neither
        the blue being looked for nor the brown floodwater this screen misses.
      </small>
    </section>
  )
}

function Evidence({ evidence, highlighted }) {
  if (!evidence?.length) return null

  return (
    <section className="evidence">
      <h3>Evidence</h3>
      <table>
        <tbody>
          {evidence.map((item) => (
            <tr
              key={item.id}
              id={`ev-${item.id}`}
              className={highlighted === item.id ? 'highlight' : ''}
            >
              <td className="ev-id">{item.id}</td>
              <td className="ev-name">
                {item.quantity.replace(/_/g, ' ')}
                {item.method && <small>{item.method}</small>}
              </td>
              <td className="ev-value">{fmt(item.value, item.unit)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}

/**
 * The zone list, sortable by rank, area, severity or latitude.
 *
 * Sorting only reorders what the backend already sent. When the list is
 * truncated - 89 zones found, 20 listed - sorting by area ascending shows
 * the smallest of the twenty largest, not the smallest of the eighty-nine.
 * The caption says so, because a sorted list looks complete whether it is
 * or not.
 */
function Zones({ zones, summary, selected, onSelect }) {
  const [sort, setSort] = useState({ key: 'rank', direction: 'asc' })

  // useMemo, not a sort in the render body: the zone list can run to
  // hundreds and this re-renders on every hover of the map.
  const ordered = useMemo(
    () => sortZones(zones, sort.key, sort.direction),
    [zones, sort.key, sort.direction],
  )

  if (!zones?.length) return null

  const arrow = sort.direction === 'asc' ? '▲' : '▼'

  return (
    <section className="zones">
      <h3>
        Zones
        {summary?.truncated && (
          <small>
            {summary.count} found, {summary.listed} largest listed
          </small>
        )}
      </h3>

      <div className="zone-sort" role="group" aria-label="Sort zones">
        {SORTS.map(({ key, label }) => (
          <button
            key={key}
            type="button"
            className={`sort-key ${sort.key === key ? 'active' : ''}`}
            aria-pressed={sort.key === key}
            onClick={() => setSort((current) => nextSort(current, key))}
          >
            {label}
            {sort.key === key && <span className="sort-arrow">{arrow}</span>}
          </button>
        ))}
      </div>

      {summary?.truncated && sort.key === 'area' && sort.direction === 'asc' && (
        <p className="zone-caveat">
          Smallest of the {summary.listed} listed, not of the {summary.count}{' '}
          found.
        </p>
      )}

      <div className="zone-list">
        {ordered.map((zone) => (
          <button
            key={zone.id}
            className={`zone ${selected === zone.id ? 'active' : ''} sev-${zone.severity}`}
            onClick={() => onSelect(zone.id)}
          >
            <span className="zone-rank">{zone.rank}</span>
            <span className="zone-area">{zone.area_km2.toLocaleString()} km²</span>
            <span className="zone-loc">
              {zone.centroid[1].toFixed(2)} N {zone.centroid[0].toFixed(2)} E
            </span>
          </button>
        ))}
      </div>
    </section>
  )
}

export default function ResultPanel({ result, selectedZone, onSelectZone, highlighted, onCite }) {
  if (!result) {
    return (
      <div className="panel result-panel empty">
        <h2>No analysis yet</h2>
        <p>
          Pick what to measure, a region and a date range. Everything reported
          here is traceable to a computed statistic.
        </p>
      </div>
    )
  }

  const noData = result.unobserved?.reason === 'no_usable_imagery'
  const pdf = pdfUrl(result)

  return (
    <div className="panel result-panel">
      <div className="panel-head">
        <h2>
          {result.analysis_label || 'Flood extent'} · {result.region?.name}
        </h2>
        {periodLine(result.period) && <p>{periodLine(result.period)}</p>}
        {pdf && (
          // A plain link rather than a fetch. The server's
          // Content-Disposition: attachment is what makes it download and
          // names the file; the `download` attribute alone would be ignored,
          // because the API is on a different origin from this page.
          <a
            className="pdf-link"
            href={pdf}
            download
            title="Finding, evidence record, caveats and provenance on paper"
          >
            Download PDF
          </a>
        )}
      </div>

      <Understood result={result} />

      {noData ? (
        <section className="no-data">
          <h3>Nothing could be observed</h3>
          <p>{result.report?.text}</p>
          <small>
            This is not a finding of absence. The satellite did not see the
            ground.
          </small>
        </section>
      ) : (
        <>
          {isUpload(result) && <UploadPreview result={result} />}
          <Report
            report={result.report}
            verification={result.verification}
            onCite={onCite}
          />
          <Coverage
            observation={result.observation}
            unobserved={result.unobserved}
          />
          <Evidence evidence={result.evidence} highlighted={highlighted} />
          <Zones
            zones={result.zones}
            summary={result.zones_summary}
            selected={selectedZone}
            onSelect={onSelectZone}
          />
        </>
      )}
    </div>
  )
}
