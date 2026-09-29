import { citationSegments } from '../api'

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
        <span>
          {report.fallback_used
            ? 'Written from a template'
            : `Written by ${report.generator_model}`}
        </span>
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
  const fraction = observation.coverage_fraction ?? 1
  const partial = fraction < 0.9

  return (
    <section className={`coverage ${partial ? 'partial' : ''}`}>
      <div className="coverage-bar">
        <div style={{ width: `${Math.min(fraction * 100, 100)}%` }} />
      </div>
      <div className="coverage-meta">
        <strong>{(fraction * 100).toFixed(1)}% of the region observed</strong>
        <span>
          {observation.sensor_used === 'sentinel-1'
            ? 'Sentinel-1 radar'
            : 'Sentinel-2 optical'}
          {' · '}
          {observation.scenes_used}/{observation.scenes_available} scenes used
        </span>
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

function Zones({ zones, summary, selected, onSelect }) {
  if (!zones?.length) return null

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
      <div className="zone-list">
        {zones.map((zone) => (
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

  return (
    <div className="panel result-panel">
      <div className="panel-head">
        <h2>
          {result.analysis_label || 'Flood extent'} · {result.region?.name}
        </h2>
        <p>
          {result.period?.post?.start} to {result.period?.post?.end}
          {result.period?.pre && ` · baseline ${result.period.pre.start}`}
        </p>
      </div>

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
