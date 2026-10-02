import { useEffect, useMemo, useState } from 'react'

import { api, assetUrl, citationSegments, gisLinks, pdfUrl } from '../api'
import {
  alignmentView,
  hasCoverage,
  isUpload,
  periodLine,
  sourceLine,
  writtenBy,
} from '../lib/ask'
import { SORTS, nextSort, sortZones } from '../lib/zonesort'
import {
  DISTRICT_SORTS,
  freshnessLine,
  isStale,
  nextPassLine,
  peopleCell,
  peopleSources,
  peopleText,
  sortDistricts,
  sumCheckLine,
} from '../lib/insights'
import { planLine } from '../lib/exportlink'
import { placesPath, placesSummary, zoneLine } from '../lib/places'
import { HINDI_LABEL, hindiPath, hindiView, withHindi } from '../lib/hindi'
import { terrainLine } from '../lib/terrain'
import { QUANTITY_TERMS } from '../lib/glossary'
import Term from './Term'
import { missingScenesLine, sceneCount, sceneWindows, truncationLine } from '../lib/scenes'

const fmt = (value, unit) => {
  if (value == null) return '—'
  if (unit === 'km2') return `${value.toLocaleString(undefined, { maximumFractionDigits: 1 })} km²`
  if (unit === 'percent') return `${value}%`
  if (unit === 'dB') return `${value} dB`
  return `${value.toLocaleString()} ${unit || ''}`.trim()
}

function Report({ result, report, verification, onCite }) {
  const [language, setLanguage] = useState('en')
  const [hindi, setHindi] = useState(null)
  const [loading, setLoading] = useState(false)
  const path = hindiPath(result)
  if (!report?.text) return null

  const showHindi = async () => {
    setLanguage('hi')
    if (hindi || !path) return
    setLoading(true)
    try {
      setHindi(hindiView(await api.hindi(path)))
    } catch (err) {
      setHindi({ reason: err.message })
    } finally {
      setLoading(false)
    }
  }
  const text = language === 'hi' && hindi?.text ? hindi.text : report.text

  return (
    <section className="report">
      <div className="report-head">
        <h3>Finding</h3>
        {path && (
          <div className="lang-switch" role="group" aria-label="Report language">
            <button type="button" className={language === 'en' ? 'active' : ''} onClick={() => setLanguage('en')}>English</button>
            <button type="button" className={language === 'hi' ? 'active' : ''} onClick={showHindi} lang="hi">हिन्दी</button>
          </div>
        )}
        <span className={`badge ${verification?.passed ? 'ok' : 'warn'}`}>
          {verification?.passed ? <Term k="verified">Verified</Term> : 'Failed verification'}
        </span>
      </div>

      {language === 'hi' && loading && <p className="hindi-note">अनुवाद हो रहा है… (translating and checking)</p>}
      {language === 'hi' && hindi?.reason && (
        <p className="hindi-note hindi-failed">Hindi not shown: {hindi.reason} The English is below.</p>
      )}
      <p className="report-text" lang={text === report.text ? 'en' : 'hi'}>
        {citationSegments(text).map((segment) =>
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

      {text !== report.text && (
        <p className="hindi-note">
          {HINDI_LABEL}{' '}
          <a href={withHindi(pdfUrl(result))} download>PDF with Hindi</a>
        </p>
      )}

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
        {measured && (
          <strong>
            {(fraction * 100).toFixed(1)}% of the region <Term k="observable_area">observed</Term>
          </strong>
        )}
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

/**
 * How old the image is. Shown near the top: for disaster staff "when was
 * this seen?" decides how much the rest is worth. With latest-pass mode, the
 * next expected pass and - when one pass covered too little - an offer to
 * include the days before it, with the dates stated.
 */
function Freshness({ acquisition, latest, onExtend }) {
  const line = freshnessLine(acquisition)
  if (!line && !latest) return null
  const stale = isStale(acquisition)
  const next = nextPassLine(latest)
  const offer = latest?.extend_offer

  return (
    <section className={`freshness ${stale ? 'freshness-stale' : ''}`}>
      {line && <p className="freshness-line">{line}</p>}
      {latest && <p className="freshness-meta">Latest-pass mode: the newest Sentinel-1 image over this area.</p>}
      {next && <p className="freshness-meta">{next}</p>}
      {offer && (
        <div className="freshness-offer">
          <p>{offer.reason}</p>
          <button type="button" onClick={() => onExtend?.(offer)}>
            Use {offer.post_start} to {offer.post_end} instead
          </button>
        </div>
      )}
    </section>
  )
}

/** People living in the flooded area: a two-model range, its sources, and
 *  - when it could not be computed - that, rather than a silent gap. */
function People({ population }) {
  if (!population) return null
  if (population.error) {
    return <section className="people people-missing"><p>{population.error}</p></section>
  }
  const text = peopleText(population.people_in_flood)
  if (!text) return null
  return (
    <section className="people">
      <h3>People living in the flooded area</h3>
      <p className="people-figure">{text}</p>
      <small>
        {peopleSources(population)} population models. Residents of flooded
        cells, not people displaced or harmed; likely an undercount, because
        part of the flood water is missed.
      </small>
    </section>
  )
}

/** Per district: worst first for a state, or the districts a drawn area
 *  falls in. Partly observed districts are marked, never quietly ranked low. */
function Districts({ districts }) {
  const [sortKey, setSortKey] = useState('flooded_km2')
  const rows = useMemo(() => sortDistricts(districts?.rows, sortKey), [districts, sortKey])
  if (!districts) return null
  if (districts.error) {
    return <section className="districts"><h3>Districts</h3><p className="people-missing">{districts.error}</p></section>
  }
  if (!rows.length) return null
  const overlap = districts.kind === 'drawn_area_overlap'

  return (
    <section className="districts">
      <h3>{overlap ? 'Districts this area falls in' : 'Districts, worst first'}</h3>
      <div className="zone-sort" role="group" aria-label="Sort districts">
        {DISTRICT_SORTS.map(({ key, label }) => (
          <button key={key} type="button"
            className={`sort-key ${sortKey === key ? 'active' : ''}`}
            aria-pressed={sortKey === key} onClick={() => setSortKey(key)}>
            {label}
          </button>
        ))}
      </div>
      <table className="district-table">
        <thead>
          <tr>
            <th>District</th><th>Flooded</th><th>% flooded</th><th>People</th>
            <th>{overlap ? '% of area' : '% seen'}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={`${row.name}|${row.state}`} className={row.low_coverage ? 'partly-seen' : ''}>
              <td>{row.name}{overlap && row.state ? <small> {row.state}</small> : null}</td>
              <td>{row.flooded_km2.toLocaleString()} km²</td>
              <td>{row.flooded_pct == null ? '—' : `${row.flooded_pct}%`}</td>
              <td>{peopleCell(row.people)}</td>
              <td>
                {overlap ? `${row.share_of_area_pct}%` : `${row.observed_pct}%`}
                {row.low_coverage && <span className="partly-flag" title="Less than 90% of this district was observed"> partly seen</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <small className="district-foot">
        {sumCheckLine(districts.sum_check)} Boundaries: {districts.boundary_source}.
      </small>
    </section>
  )
}

/**
 * Villages and roads in the flood zones, looked up in OpenStreetMap when the
 * result opens. Names, not measurements: shown with their caveats and kept
 * out of the evidence record.
 */
function Places({ result }) {
  const path = placesPath(result)
  const [block, setBlock] = useState(null)
  // Starts loading at mount: the component is keyed by result, so a new
  // result is a new mount, and no setState is needed inside the effect.
  const [loading, setLoading] = useState(Boolean(path))

  useEffect(() => {
    if (!path) return undefined
    let live = true
    api.places(path)
      .then((data) => { if (live) setBlock(data) })
      .catch((err) => { if (live) setBlock({ error: err.message }) })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [path])

  if (!path) return null
  if (loading && !block) return <p className="places-loading">Looking up villages and roads in OpenStreetMap…</p>
  if (!block) return null
  if (block.error) return <p className="places-missing">Village and road names unavailable: {block.error}</p>

  const zones = block.zones.filter((z) => z.places.length || z.roads.length)
  return (
    <section className="places">
      <h3>Villages and roads in the flood zones</h3>
      <p className="places-summary">{placesSummary(block)}</p>
      <table className="places-table">
        <thead><tr><th>Zone</th><th>Villages and towns</th><th>Main roads crossing it</th></tr></thead>
        <tbody>
          {zones.map((z) => {
            const line = zoneLine(z)
            return (
              <tr key={z.zone}>
                <td>{z.zone}</td><td>{line.places}</td><td>{line.roads}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <small className="places-foot">
        * within {block.buffer_m} m of the zone, not inside it. {block.attribution}.{' '}
        <a href={`${api.base}/analyze/${result.request_id}/export/places.csv`} download>CSV</a>
      </small>
      <ul className="places-caveats">
        {block.caveats.map((c) => <li key={c}>{c}</li>)}
      </ul>
    </section>
  )
}

/**
 * GIS files for analysts: zones as GeoJSON, KML and CSV, districts as CSV,
 * and the flood map as a GeoTIFF of codes. The GeoTIFF is rebuilt on Earth
 * Engine, so its delivery scale is asked for when the menu opens and stated
 * before anyone waits for it.
 */
function Downloads({ result }) {
  const links = gisLinks(result)
  const [plan, setPlan] = useState(null)
  const [planError, setPlanError] = useState('')
  if (!links.length) return null
  const tif = links.find((l) => l.key === 'geotiff')

  const onToggle = (event) => {
    if (!event.currentTarget.open || !tif || plan || planError) return
    api.floodPlan(tif.planPath).then(setPlan).catch((err) => setPlanError(err.message))
  }

  return (
    <details className="downloads" onToggle={onToggle}>
      <summary>GIS files</summary>
      <ul>
        {links.map((link) => {
          const blocked = link.key === 'geotiff' && (planError || plan?.scale_m === null)
          return (
            <li key={link.key}>
              {blocked ? (
                <span className="download-blocked">{link.label}</span>
              ) : (
                <a href={link.url} download>{link.label}{link.key === 'geotiff' && plan?.scale_m ? ` (${plan.scale_m} m)` : ''}</a>
              )}
              <small> {link.hint}</small>
              {link.key === 'geotiff' && (planError || planLine(plan)) && (
                <small className="download-note">{planError || planLine(plan)}</small>
              )}
            </li>
          )
        })}
      </ul>
      <small className="download-foot">
        Each file carries the request ID {result.request_id}. In the GeoTIFF, 1 is
        flooded, 0 seen and dry, 2 permanent water,{' '}
        {result.terrain?.applied ? '3 dark but excluded by the terrain check, ' : ''}255 not observed.
      </small>
    </details>
  )
}

/**
 * Exactly which satellite images the result came from, collapsed by default:
 * most readers need the dates (shown above), researchers need the IDs. The
 * Earth Engine snippet loads the same scenes again.
 */
function Scenes({ result }) {
  const [copied, setCopied] = useState(null)
  const windows = sceneWindows(result.scenes)
  const missing = missingScenesLine(result)
  if (!windows.length) {
    return missing ? <p className="scenes-missing">{missing}</p> : null
  }

  const copy = async (key, text) => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(key)
    } catch {
      setCopied(null)
    }
  }

  return (
    <details className="scenes">
      <summary>Satellite scenes used ({sceneCount(result.scenes)})</summary>
      {windows.map((w) => (
        <div key={w.key} className="scene-window">
          <h4>
            {w.title}
            {w.collection && <small> · {w.total} from {w.collection}</small>}
          </h4>
          {w.error ? (
            <p className="people-missing">{w.error}</p>
          ) : (
            <>
              <table className="scene-table">
                <thead>
                  <tr><th>Scene ID</th><th>Acquired</th><th>Satellite</th><th>Detail</th></tr>
                </thead>
                <tbody>
                  {w.rows.map((row) => (
                    <tr key={row.id}>
                      <td className="scene-id">{row.id}</td>
                      <td>{row.when}</td>
                      <td>{row.platform}</td>
                      <td>{row.detail}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {truncationLine(w) && <small className="scene-note">{truncationLine(w)}</small>}
              {w.snippet && (
                <div className="scene-snippet">
                  <button type="button" onClick={() => copy(w.key, w.snippet)}>
                    {copied === w.key ? 'Copied' : 'Copy Earth Engine code'}
                  </button>
                  <pre>{w.snippet}</pre>
                </div>
              )}
            </>
          )}
        </div>
      ))}
    </details>
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
                <Term k={QUANTITY_TERMS[item.quantity]}>{item.quantity.replace(/_/g, ' ')}</Term>
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
            <span className="zone-area">
              {zone.area_km2.toLocaleString()} km²
              {zone.population && <small className="zone-people"> · {peopleCell(zone.population)} people</small>}
            </span>
            <span className="zone-loc">
              {zone.centroid[1].toFixed(2)} N {zone.centroid[0].toFixed(2)} E
            </span>
          </button>
        ))}
      </div>
    </section>
  )
}

export default function ResultPanel({ result, selectedZone, onSelectZone, highlighted, onCite, onExtend }) {
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
        {/* Keyed by result: a plan fetched for one analysis must not show under another. */}
        <Downloads key={result.request_id} result={result} />
      </div>

      <Understood result={result} />
      <Freshness acquisition={result.acquisition} latest={result.latest} onExtend={onExtend} />

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
            key={`report-${result.request_id}`}
            result={result}
            report={result.report}
            verification={result.verification}
            onCite={onCite}
          />
          <People population={result.population} />
          {terrainLine(result.terrain) && (
            <p className={`terrain-line ${result.terrain.applied ? '' : 'terrain-off'}`}>
              {terrainLine(result.terrain)}
            </p>
          )}
          <Coverage
            observation={result.observation}
            unobserved={result.unobserved}
          />
          <Evidence evidence={result.evidence} highlighted={highlighted} />
          <Districts districts={result.districts} />
          <Zones
            zones={result.zones}
            summary={result.zones_summary}
            selected={selectedZone}
            onSelect={onSelectZone}
          />
          <Places key={`places-${result.request_id}`} result={result} />
          <Scenes key={result.request_id} result={result} />
        </>
      )}
    </div>
  )
}
