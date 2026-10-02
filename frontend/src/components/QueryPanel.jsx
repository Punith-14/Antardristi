import { RELIABILITY } from '../api'
import { periodLabel } from '../lib/swipe'
import { accuracyLine, availability, findOption, limitsLine, methodOptions } from '../lib/methods'
import { effectiveOption, terrainAvailable } from '../lib/terrain'
import Term from './Term'

const PRESETS = [
  {
    label: 'Kerala floods, August 2018',
    analysisType: 'flood_extent',
    region: 'kerala',
    postStart: '2018-08-15',
    postEnd: '2018-08-25',
    preStart: '2018-02-01',
    preEnd: '2018-04-30',
  },
  {
    label: 'Punjab vegetation, kharif',
    analysisType: 'vegetation_health',
    region: 'punjab',
    postStart: '2023-09-01',
    postEnd: '2023-09-30',
  },
  {
    label: 'Kerala surface water',
    analysisType: 'water_extent',
    region: 'kerala',
    postStart: '2023-01-01',
    postEnd: '2023-03-31',
  },
  {
    label: 'Bengaluru growth, 2019 to 2023',
    analysisType: 'built_up',
    region: 'bangalore urban',
    postStart: '2023-01-01',
    postEnd: '2023-03-31',
    preStart: '2019-01-01',
    preEnd: '2019-03-31',
  },
]

export default function QueryPanel({
  catalogue,
  form,
  onChange,
  onSubmit,
  onSeries,
  onLatest,
  onPreset,
  status,
  error,
}) {
  const analyses = catalogue?.surface || []
  const flood = catalogue?.flood
  const isFlood = form.analysisType === 'flood_extent'
  const options = methodOptions(catalogue)
  const option = findOption(options, form.floodMethod)
  // What will actually run - with the terrain check, if it is on - so the
  // accuracy shown is the one the result will quote.
  const shown = effectiveOption(option, catalogue, form)
  // Shown beside the buttons they disable, so an option that cannot run says
  // why instead of quietly doing nothing.
  const singleCheck = availability(option, form, 'single')
  const latestCheck = availability(option, form, 'latest')
  const seriesCheck = availability(option, form, 'series')
  const selected = isFlood
    ? null
    : analyses.find((a) => a.type === form.analysisType)

  const set = (key) => (event) => onChange({ ...form, [key]: event.target.value })

  return (
    <form
      className="panel query-panel"
      onSubmit={(event) => {
        event.preventDefault()
        onSubmit()
      }}
    >
      <div className="panel-head">
        <h2>Run an analysis</h2>
        <p>Every result reports its own measured accuracy.</p>
      </div>

      <label className="field">
        <span>What to measure</span>
        <select value={form.analysisType} onChange={set('analysisType')}>
          {flood && (
            <option value="flood_extent">
              Flood extent — measured; accuracy by method below
            </option>
          )}
          {analyses.map((item) => (
            <option key={item.type} value={item.type}>
              {item.label}
              {item.iou != null
                ? ` — ${RELIABILITY[item.reliability].label}, IoU ${item.iou}`
                : ' — not validated'}
            </option>
          ))}
        </select>
      </label>

      {isFlood && (
        <div className="method-choice">
          <label className="field">
            <span>Sensor and method</span>
            <select value={option.key} onChange={set('floodMethod')}>
              {options.map((o) => (
                <option key={o.key} value={o.key}>
                  {o.label}{o.validation?.iou != null ? ` — IoU ${o.validation.iou}` : ''}
                </option>
              ))}
            </select>
          </label>
          <div className={`reliability reliability-${shown.validation ? 'moderate' : 'unvalidated'}`}>
            <strong>{shown.validation ? 'Measured' : 'Not validated'}</strong>
            <span>{accuracyLine(shown)}</span>
            <small className="term-legend">
              <Term k="iou">IoU</Term> · <Term k="precision">precision</Term> ·{' '}
              <Term k="recall">recall</Term> - hover for what each means
            </small>
            {shown.rule && <p className="method-rule">Rule: {shown.rule}.</p>}
            {option.caveat && <p>{option.caveat}</p>}
            {limitsLine(option) && <p className="method-limits">{limitsLine(option)}</p>}
          </div>
          {option.sensor === 'sentinel-2' && (
            <label className="field">
              <span>Cloud limit (% per scene)</span>
              <input
                type="number" min="1" max="100" step="1"
                value={form.cloudLimit ?? ''}
                placeholder={String(option.default_cloud_limit ?? 40)}
                onChange={set('cloudLimit')}
              />
              <small>
                Scenes cloudier than this are left out. Raising it lets in more
                scenes and more cloud; cloudy pixels are always counted as
                unobserved, never as dry.
              </small>
            </label>
          )}
          {terrainAvailable(catalogue, option) && (
            <label className="terrain-toggle">
              <input
                type="checkbox"
                checked={form.terrainCheck !== false}
                onChange={(event) => onChange({ ...form, terrainCheck: event.target.checked })}
              />
              <span>
                Terrain check: do not count dark ground {flood.terrain.text}.
                <small>
                  Without it: IoU {option.validation?.iou}. The ground it removes is
                  still reported and shown on the map.
                </small>
              </span>
            </label>
          )}
          {!singleCheck.ok && <p className="method-blocked">{singleCheck.reason}</p>}
        </div>
      )}

      {selected && (
        <div className={`reliability reliability-${selected.reliability}`}>
          <strong>{RELIABILITY[selected.reliability]?.label}</strong>
          {selected.iou != null && <span>IoU {selected.iou}</span>}
          {selected.caveat && <p>{selected.caveat}</p>}
        </div>
      )}

      <label className="field">
        <span>Region</span>
        <input
          value={form.region}
          onChange={set('region')}
          placeholder="kerala, punjab, kendrapara..."
        />
        <small>
          Any Indian state or district. Boundaries are from 2015, so districts
          created since then will not resolve.
        </small>
      </label>

      <div className="field-row">
        <label className="field">
          <span>From</span>
          <input type="date" value={form.postStart} onChange={set('postStart')} />
        </label>
        <label className="field">
          <span>To</span>
          <input type="date" value={form.postEnd} onChange={set('postEnd')} />
        </label>
      </div>

      {/* `open` only sets the INITIAL state - collapsing a <details> changes
          the DOM directly and React will not reopen it while the prop value
          stays the same. So a baseline could be active and out of sight at the
          same time. The summary now states it, which is true whether the
          section is open or shut. */}
      <details className="compare" open={Boolean(form.preStart)}>
        <summary>
          Compare against an earlier period
          {form.preStart && (
            <span className="compare-active">
              {periodLabel({ start: form.preStart, end: form.preEnd })}
            </span>
          )}
        </summary>
        <div className="field-row">
          <label className="field">
            <span>Baseline from</span>
            <input type="date" value={form.preStart} onChange={set('preStart')} />
          </label>
          <label className="field">
            <span>Baseline to</span>
            <input type="date" value={form.preEnd} onChange={set('preEnd')} />
          </label>
        </div>
        <small>
          With a baseline you get gained and lost area separately, and zones are
          drawn around what changed.
        </small>
        {form.preStart && (
          <button
            type="button"
            className="clear-baseline"
            onClick={() => onChange({ ...form, preStart: '', preEnd: '' })}
          >
            Remove the baseline
          </button>
        )}
      </details>

      <button className="run" type="submit" disabled={status === 'loading' || (isFlood && !singleCheck.ok)}>
        {status === 'loading' ? 'Analysing…' : 'Run analysis'}
      </button>

      {isFlood && onLatest && (
        <div className="series-run">
          <button type="button" disabled={status === 'loading' || !latestCheck.ok} onClick={onLatest}>
            Latest radar image
          </button>
          <small>
            {latestCheck.ok
              ? 'Ignores the dates above and analyses the newest Sentinel-1 pass over the area. The result says how old that image is.'
              : latestCheck.reason}
          </small>
        </div>
      )}

      {isFlood && onSeries && (
        <div className="series-run">
          <button type="button" disabled={status === 'loading' || !seriesCheck.ok} onClick={onSeries}>
            Run month by month
          </button>
          <small>
            {seriesCheck.ok
              ? `One full analysis per calendar month from From to To, up to 12, all with ${option.sensor === 'sentinel-2' ? 'Sentinel-2' : 'Sentinel-1'}. Slow the first time; each month is cached after that. Months with no imagery show as gaps, never as zero.`
              : seriesCheck.reason}
          </small>
        </div>
      )}

      {error && <p className="error">{error}</p>}

      <div className="presets">
        <span>Try</span>
        {PRESETS.map((preset) => (
          <button key={preset.label} type="button" onClick={() => onPreset(preset)}>
            {preset.label}
          </button>
        ))}
      </div>
    </form>
  )
}
