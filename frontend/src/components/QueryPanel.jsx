import { useState } from 'react'

import { accuracyLine, availability, findOption, limitsLine, methodOptions } from '../lib/methods'
import { PRESETS, WHEN_MODES, formForMode, modeOf } from '../lib/presets'
import { reliabilityView, scoreSentence } from '../lib/reliability'
import { effectiveOption, terrainAvailable } from '../lib/terrain'
import { dateProblem, firstDay, lastDay, monthOf, wholeMonths } from '../lib/months'
import PlaceInput from './PlaceInput'
import Term from './Term'

const DESCRIPTIONS = {
  flood_extent: 'Where flood water is, how many people live there, and which villages and roads it reaches.',
  water_extent: 'Rivers, lakes and reservoirs on the chosen dates.',
  vegetation_health: 'How green and healthy plants are, from Sentinel-2 images.',
  crop_stress: 'Fields and plants doing worse than in a normal season.',
  green_cover: 'Trees, crops and parks.',
  bare_ground: 'Bare soil, sand and rock.',
  built_up: 'Towns and buildings, and how they grow.',
}

function Step({ n, title, children }) {
  return (
    <section className="form-step">
      <h3 className="step-title"><span>{n}</span>{title}</h3>
      {children}
    </section>
  )
}

/** The badge, a plain sentence, and the numbers behind it on request. */
function Reliability({ view, children }) {
  return (
    <div className={`rel-box rel-box-${view.tone}`}>
      <div className="rel-line">
        <span className={`rel rel-${view.tone}`}>{view.label}</span>
        <span>{view.text}</span>
      </div>
      <details>
        <summary>What does this mean?</summary>
        <p>{scoreSentence(view)}</p>
        {children}
      </details>
    </div>
  )
}

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
  drawnArea,
  onClearArea,
  disabled = false,
}) {
  const [localError, setLocalError] = useState('')
  const analyses = catalogue?.surface || []
  const flood = catalogue?.flood
  const isFlood = form.analysisType === 'flood_extent'
  const mode = modeOf(form)
  const options = methodOptions(catalogue)
  const option = findOption(options, form.floodMethod)
  // What will actually run - with the terrain check, if it is on - so the
  // accuracy shown is the one the result will quote.
  const shown = effectiveOption(option, catalogue, form)
  const singleCheck = availability(option, form, 'single')
  const latestCheck = availability(option, form, 'latest')
  const seriesCheck = availability(option, form, 'series')
  const selected = isFlood ? null : analyses.find((a) => a.type === form.analysisType)
  const view = isFlood
    ? reliabilityView({ iou: shown.validation?.iou })
    : reliabilityView({ grade: selected?.reliability, iou: selected?.iou })
  const busy = disabled || status === 'loading'

  const set = (key) => (event) => {
    setLocalError('')
    onChange({ ...form, [key]: event.target.value })
  }

  const setMode = (key) => {
    setLocalError('')
    onChange({
      ...form, when: key,
      ...(key === 'compare' ? {} : { preStart: '', preEnd: '' }),
      // Month by month runs whole months: widen whatever dates were there.
      ...(key === 'monthly' && form.postStart && form.postEnd ? wholeMonths(form.postStart, form.postEnd) : {}),
    })
  }

  const setMonth = (which) => (event) => {
    setLocalError('')
    const month = event.target.value
    onChange({ ...form, [which]: which === 'postStart' ? firstDay(month) : lastDay(month) })
  }

  const setType = (event) => {
    const analysisType = event.target.value
    // Month by month exists for flood only.
    const when = analysisType !== 'flood_extent' && mode === 'monthly' ? 'one' : mode
    setLocalError('')
    onChange({ ...form, analysisType, when })
  }

  const submit = (event) => {
    event.preventDefault()
    if (!drawnArea && !(form.region || '').trim()) {
      setLocalError('Type a state or district, or draw an area on the map.')
      return
    }
    const problem = dateProblem(form, mode)
    if (problem) {
      setLocalError(problem)
      return
    }
    if (mode === 'monthly') onSeries()
    else onSubmit(formForMode({ ...form, when: mode }))
  }

  const runLabel = status === 'loading' ? 'Working…'
    : mode === 'compare' ? 'Compare the two periods'
      : mode === 'monthly' ? 'Run month by month' : 'Run analysis'
  const blocked = isFlood && (mode === 'monthly' ? !seriesCheck.ok : !singleCheck.ok)

  return (
    <form className="panel query-panel" onSubmit={submit}>
      <div className="panel-head">
        <h2>New analysis</h2>
        <p>Every result reports its own measured accuracy.</p>
      </div>

      <Step n={1} title="What to measure">
        <select value={form.analysisType} onChange={setType} aria-label="What to measure">
          {flood && <option value="flood_extent">Flood extent</option>}
          {analyses.map((item) => (
            <option key={item.type} value={item.type}>{item.label}</option>
          ))}
        </select>
        <p className="step-desc">{DESCRIPTIONS[form.analysisType] || selected?.description || ''}</p>
        <Reliability view={view}>
          {isFlood ? (
            <>
              <p className="rel-numbers">{accuracyLine(shown)}</p>
              <small className="term-legend">
                <Term k="iou">IoU</Term> · <Term k="precision">precision</Term> ·{' '}
                <Term k="recall">recall</Term> - hover for what each means
              </small>
              {shown.rule && <p className="method-rule">Rule: {shown.rule}.</p>}
              {option.caveat && <p>{option.caveat}</p>}
            </>
          ) : (
            selected?.caveat && <p>{selected.caveat}</p>
          )}
        </Reliability>
      </Step>

      <Step n={2} title="Area">
        {drawnArea ? (
          <div className="area-chip">
            <span>Using the area you {drawnArea.kind === 'boundary' ? 'uploaded' : 'drew'} on the map</span>
            <button type="button" onClick={onClearArea}>Use a place name</button>
          </div>
        ) : (
          <>
            <PlaceInput value={form.region} disabled={busy}
              onChange={(region) => { setLocalError(''); onChange({ ...form, region }) }} />
            <small className="step-hint">
              Any Indian state or district. Or draw a box, circle or shape on the map, or upload a
              boundary file, with the buttons at the map's top right.
            </small>
          </>
        )}
      </Step>

      <Step n={3} title="When">
        <div className="seg" role="tablist" aria-label="Dates">
          {WHEN_MODES.filter((m) => isFlood || !m.floodOnly).map((m) => (
            <button key={m.key} type="button" role="tab" aria-selected={mode === m.key}
              className={mode === m.key ? 'on' : ''} onClick={() => setMode(m.key)}>{m.label}</button>
          ))}
        </div>

        {mode === 'compare' && (
          <>
            <small className="date-label">Before</small>
            <div className="field-row">
              <input type="date" value={form.preStart} onChange={set('preStart')} aria-label="Before, from" />
              <input type="date" value={form.preEnd} onChange={set('preEnd')} aria-label="Before, to" />
            </div>
            <small className="date-label">After</small>
          </>
        )}
        {mode === 'monthly' ? (
          <div className="field-row">
            <label className="month-field">
              <small className="date-label">First month</small>
              <input type="month" value={monthOf(form.postStart)} onChange={setMonth('postStart')}
                aria-label="First month" />
            </label>
            <label className="month-field">
              <small className="date-label">Last month</small>
              <input type="month" value={monthOf(form.postEnd)} onChange={setMonth('postEnd')}
                aria-label="Last month" />
            </label>
          </div>
        ) : (
          <div className="field-row">
            <input type="date" value={form.postStart} onChange={set('postStart')} aria-label="From" />
            <input type="date" value={form.postEnd} onChange={set('postEnd')} aria-label="To" />
          </div>
        )}
        {mode === 'one' && isFlood && onLatest && (
          <button type="button" className="link-btn" disabled={busy || !latestCheck.ok} onClick={onLatest}
            title={latestCheck.ok ? 'Analyses the newest Sentinel-1 pass over the area' : latestCheck.reason}>
            Use the latest radar image instead
          </button>
        )}
        <small className="step-hint">
          {mode === 'compare' && 'You get the area gained and lost, and the map gets a slider to wipe between the two periods.'}
          {mode === 'monthly' && (seriesCheck.ok
            ? 'One full analysis per month, up to 12, as a bar chart. Months with no imagery show as gaps, never as zero.'
            : seriesCheck.reason)}
          {mode === 'one' && !latestCheck.ok && isFlood && latestCheck.reason}
        </small>
      </Step>

      {isFlood && (
        <Step n={4} title="Satellite and method">
          <select value={option.key} onChange={set('floodMethod')} aria-label="Satellite and method">
            {options.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
          </select>
          {limitsLine(option) && <small className="step-hint">{limitsLine(option)}</small>}
          {option.sensor === 'sentinel-2' && (
            <label className="field inline-field">
              <span>Cloud limit (% per image)</span>
              <input type="number" min="1" max="100" step="1" value={form.cloudLimit ?? ''}
                placeholder={String(option.default_cloud_limit ?? 40)} onChange={set('cloudLimit')} />
            </label>
          )}
          {terrainAvailable(catalogue, option) && (
            <label className="terrain-toggle">
              <input type="checkbox" checked={form.terrainCheck !== false}
                onChange={(event) => onChange({ ...form, terrainCheck: event.target.checked })} />
              <span>Terrain check: do not count dark ground {flood.terrain.text}.</span>
            </label>
          )}
          {mode !== 'monthly' && !singleCheck.ok && <p className="method-blocked">{singleCheck.reason}</p>}
        </Step>
      )}

      <button className="btn btn-primary btn-block run-btn" type="submit" disabled={busy || blocked}>
        {status === 'loading' && <span className="spinner" />}{runLabel}
      </button>

      {(localError || error) && <p className="error" role="alert">{localError || error}</p>}

      <div className="presets">
        <span>Try an example</span>
        {PRESETS.map((preset) => (
          <button key={preset.key} type="button" disabled={busy} onClick={() => onPreset(preset)}>
            {preset.label}
          </button>
        ))}
      </div>
    </form>
  )
}
