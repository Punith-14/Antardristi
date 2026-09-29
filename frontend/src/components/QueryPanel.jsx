import { RELIABILITY } from '../api'
import { periodLabel } from '../lib/swipe'

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
  onPreset,
  status,
  error,
}) {
  const analyses = catalogue?.surface || []
  const flood = catalogue?.flood
  const selected =
    form.analysisType === 'flood_extent'
      ? {
          reliability: 'moderate',
          iou: flood?.validation?.iou,
          caveat:
            'Sentinel-1 radar sees through cloud, which is why this is the flood sensor.',
        }
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
              Flood extent — validated, IoU {flood.validation.iou}
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

      <button className="run" type="submit" disabled={status === 'loading'}>
        {status === 'loading' ? 'Analysing…' : 'Run analysis'}
      </button>

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
