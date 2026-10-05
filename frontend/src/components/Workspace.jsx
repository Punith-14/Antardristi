import { useCallback, useEffect, useRef, useState } from 'react'

import { api, runAnalysis, runSeries } from '../api'
import { navigate, toast } from '../hooks'
import { describeFootprint } from '../lib/bbox'
import { boundaryArea, boundaryFileProblem } from '../lib/boundary'
import { DEFAULT_METHOD, availability, findOption, methodOptions, requestFields } from '../lib/methods'
import { findPreset } from '../lib/presets'
import { hrefFor } from '../lib/router'
import { VIEW_ONLY } from '../lib/session'
import AskPanel from './AskPanel'
import Icon from './Icon'
import MapView from './MapView'
import QueryPanel from './QueryPanel'
import ResultPanel from './ResultPanel'
import SeriesPanel from './SeriesPanel'

const DEFAULT_FORM = {
  analysisType: 'flood_extent',
  region: 'kerala',
  when: 'one',
  postStart: '2018-08-15',
  postEnd: '2018-08-25',
  // No baseline unless "Before and after" is chosen: a baseline that was set
  // and out of sight once gave someone a change figure they never asked for.
  preStart: '',
  preEnd: '',
  floodMethod: DEFAULT_METHOD,
  cloudLimit: '',
}

/** The live steps of a running analysis, with a clock. */
function Progress({ progress }) {
  const [seconds, setSeconds] = useState(0)
  useEffect(() => {
    const id = setInterval(() => setSeconds((s) => s + 1), 1000)
    return () => clearInterval(id)
  }, [])
  const steps = progress?.steps || []
  return (
    <div className="panel progress-card" role="status" aria-live="polite">
      <div className="progress-head">
        <span className="spinner dark" />
        <strong>Working on it</strong>
        <span className="progress-time">{seconds} s</span>
      </div>
      <ol className="progress-steps">
        {steps.length === 0 && <li className="now">Starting…</li>}
        {steps.map((step, i) => (
          <li key={step.text} className={i === steps.length - 1 ? 'now' : 'done'}>{step.text}</li>
        ))}
      </ol>
      <div className="progress-bar"><i /></div>
      <small>Satellite analyses take one to two minutes the first time; repeats are instant.</small>
    </div>
  )
}

/**
 * New analysis and Ask share one workspace - the map, the result and the
 * history of this visit - so a question's answer and a form's result land in
 * the same place. It stays mounted while other pages are open, so a running
 * analysis keeps going and the map is not rebuilt.
 */
export default function Workspace({ mode, params, visible, catalogue, catalogueError, mayRun, onBusy }) {
  const [progress, setProgress] = useState(null)
  const track = useCallback((steps, jobStatus) => setProgress({ steps, status: jobStatus }), [])
  const [form, setForm] = useState(DEFAULT_FORM)
  const [result, setResult] = useState(null)
  const [status, setStatus] = useState('idle')
  const [error, setError] = useState('')
  const [selectedZone, setSelectedZone] = useState(null)
  const [highlighted, setHighlighted] = useState(null)
  const [drawnArea, setDrawnArea] = useState(null)
  const [series, setSeries] = useState(null)
  const [opening, setOpening] = useState(false)
  const [askError, setAskError] = useState(null)
  const [uploadFailure, setUploadFailure] = useState('')
  const [boundaryChoices, setBoundaryChoices] = useState(null)
  const [boundaryMessage, setBoundaryMessage] = useState('')
  // The job behind the result on screen ({id, saved, title, note}), so it can be saved.
  const [job, setJob] = useState(null)
  const [captureMode, setCaptureMode] = useState(null)
  const [pictureTick, setPictureTick] = useState(0)
  const handled = useRef(new Set())

  useEffect(() => { onBusy?.(status === 'loading') }, [status, onBusy])

  const floodOption = findOption(methodOptions(catalogue), form.floodMethod)

  const finish = useCallback((data, label, jobInfo) => {
    setSeries(null)
    setJob(jobInfo || data?._job || null)
    setCaptureMode(null)
    setResult(data)
    setSelectedZone(null)
    setStatus('done')
    toast(label ? `Ready: ${label}` : 'Your result is ready', 'ok')
  }, [])

  const submit = useCallback(async (override) => {
    const query = override || form
    let floodChoice
    if (query.analysisType === 'flood_extent') {
      const option = findOption(methodOptions(catalogue), query.floodMethod)
      const check = availability(option, query, query.latest ? 'latest' : 'single')
      if (!check.ok) {
        setStatus('error')
        setError(check.reason)
        return
      }
      floodChoice = requestFields(option, query)
    }
    setStatus('loading')
    setProgress(null)
    setError('')
    setSelectedZone(null)
    try {
      const data = await runAnalysis({ ...query, area: drawnArea, floodChoice }, track)
      finish(data, `${data.analysis_label || 'Flood extent'} · ${describeFootprint(data.region)}`)
    } catch (err) {
      setStatus('error')
      setError(err.message)
      toast(err.message, 'error')
    }
  }, [form, drawnArea, catalogue, track, finish])

  const askQuestion = useCallback(async (question) => {
    setStatus('loading')
    setError('')
    setAskError(null)
    setProgress(null)
    try {
      const data = await api.ask({ question, area: drawnArea }, track)
      finish(data, question.length > 50 ? `${question.slice(0, 47)}…` : question)
    } catch (err) {
      setStatus('error')
      setAskError(err)
    }
  }, [drawnArea, track, finish])

  const uploadImage = useCallback(async (file) => {
    setStatus('loading')
    setError('')
    setUploadFailure('')
    try {
      finish(await api.uploadImage(file), `Photo · ${file.name}`, null)
    } catch (err) {
      setStatus('error')
      setUploadFailure(err.message)
    }
  }, [finish])

  const submitSeries = useCallback(async () => {
    const check = availability(floodOption, form, 'series')
    if (!check.ok) {
      setStatus('error')
      setError(check.reason)
      return
    }
    setStatus('loading')
    setError('')
    setSelectedZone(null)
    setProgress(null)
    try {
      const data = await runSeries({ ...form, area: drawnArea, floodChoice: requestFields(floodOption, form) }, track)
      setSeries(data)
      setResult(null)
      setStatus('done')
      toast('Monthly series ready', 'ok')
    } catch (err) {
      setStatus('error')
      setError(err.message)
    }
  }, [form, drawnArea, floodOption, track])

  const openMonth = useCallback(async (requestId) => {
    setOpening(true)
    setError('')
    try {
      setResult(await api.analysis(requestId))
      setSelectedZone(null)
    } catch (err) {
      setError(err.message)
    } finally {
      setOpening(false)
    }
  }, [])

  const uploadBoundary = useCallback(async (file) => {
    const problem = boundaryFileProblem(file)
    setBoundaryChoices(null)
    if (problem) {
      setBoundaryMessage(problem)
      return
    }
    setBoundaryMessage(`Reading ${file.name}…`)
    try {
      const body = await api.parseBoundary(file)
      if (body.boundary) {
        setDrawnArea(boundaryArea(body.boundary))
        setBoundaryMessage('')
      } else {
        setBoundaryChoices(body)
        setBoundaryMessage('')
      }
    } catch (err) {
      setBoundaryMessage(err.message)
    }
  }, [])

  const pickBoundary = useCallback(async (index) => {
    try {
      const boundary = await api.boundaryFeature(boundaryChoices.file.sha256, index)
      setDrawnArea(boundaryArea(boundary))
      setBoundaryChoices(null)
      setBoundaryMessage('')
    } catch (err) {
      setBoundaryMessage(err.message)
    }
  }, [boundaryChoices])

  const submitLatest = useCallback(() => submit({ ...form, latest: true, preStart: '', preEnd: '' }), [form, submit])

  const extendLatest = useCallback((offer) => {
    const next = { ...form, when: 'one', postStart: offer.post_start, postEnd: offer.post_end,
      preStart: '', preEnd: '', latest: false }
    setForm(next)
    submit(next)
  }, [form, submit])

  const applyPreset = useCallback((preset) => {
    const next = { ...DEFAULT_FORM, ...preset }
    setForm(next)
    setDrawnArea(null)
    submit(next)
  }, [submit])

  const saveJob = useCallback(async (title, note) => {
    if (!job?.id) return
    try {
      const row = await api.saveJob(job.id, title, note)
      setJob({ id: job.id, saved: true, title: row.title, note: row.note })
      toast('Saved. It will stay in History until you remove it.', 'ok')
    } catch (err) {
      toast(err.message, 'error')
      throw err
    }
  }, [job])

  const unsaveJob = useCallback(async () => {
    if (!job?.id) return
    try {
      await api.unsaveJob(job.id)
      setJob({ ...job, saved: false })
      toast('No longer saved: it will be removed after a week.', 'ok')
    } catch (err) {
      toast(err.message, 'error')
    }
  }, [job])

  const capture = useCallback(async (area) => {
    const requestId = result?.request_id
    setCaptureMode(null)
    if (!requestId) return
    const shape = area.kind === 'circle'
      ? { centre: [area.centre.lng, area.centre.lat], radius_km: area.radiusKm }
      : { bbox: area.bbox }
    toast('Drawing your picture…', 'info')
    try {
      await api.capturePicture(requestId, { ...shape, job_id: job?.id || null })
      setPictureTick((t) => t + 1)
      toast('Added to the report. Give it a caption in the Pictures tab.', 'ok')
    } catch (err) {
      toast(err.message, 'error')
    }
  }, [result, job])

  const jumpToEvidence = (id) => {
    setHighlighted(id)
    document.getElementById(`ev-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    setTimeout(() => setHighlighted(null), 2000)
  }

  // Links from Home and History: ?job= opens a past result, ?example= runs a
  // worked example, ?q= asks a question. Each is acted on once, then the
  // address is tidied so a refresh does not repeat it.
  const paramKey = JSON.stringify(params)
  useEffect(() => {
    if (!visible) return
    const key = `${mode}|${paramKey}`
    if (handled.current.has(key)) return
    const tidy = () => navigate(hrefFor(mode))
    // Deferred a tick so the work starts outside the effect; not cancelled on
    // re-render, because the key is already marked as handled.
    if (params.job) {
      handled.current.add(key)
      setTimeout(() => {
        setStatus('loading')
        setProgress(null)
        api.job(params.job)
          .then((job) => {
            if (job.status !== 'done' || !job.result) throw new Error('That analysis has no stored result.')
            if (job.kind === 'series') {
              setSeries(job.result)
              setResult(null)
              setStatus('done')
            } else {
              finish(job.result, job.result.region?.name,
                { id: job.id, saved: job.saved, title: job.title, note: job.note })
            }
          })
          .catch((err) => { setStatus('error'); setError(err.message) })
          .finally(tidy)
      }, 0)
    } else if (params.example && mayRun) {
      handled.current.add(key)
      const preset = findPreset(params.example)
      setTimeout(() => { if (preset) applyPreset(preset); tidy() }, 0)
    } else if (params.q && mode === 'ask' && mayRun) {
      handled.current.add(key)
      setTimeout(() => { askQuestion(params.q); tidy() }, 0)
    } else if (params.region && mode === 'new') {
      // From the Satellites page: the place is filled in, the user chooses the rest.
      handled.current.add(key)
      setTimeout(() => {
        setDrawnArea(null)
        setForm((current) => ({ ...current, region: params.region }))
        tidy()
      }, 0)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible, mode, paramKey, mayRun, applyPreset, askQuestion, finish])

  const showRight = status === 'loading' || result || series

  return (
    <div className={`workspace ${showRight ? 'has-result' : ''}`} hidden={!visible}>
      <aside className="ws-side">
        {!mayRun && <p className="view-only">{VIEW_ONLY}</p>}
        {catalogueError && <p className="error">{catalogueError}</p>}
        {mode === 'ask' ? (
          <AskPanel
            key="ask-panel"
            initialQuestion={params.q || ''}
            disabled={!mayRun}
            onAsk={askQuestion}
            onUpload={uploadImage}
            status={status}
            askError={askError}
            uploadFailure={uploadFailure}
            drawnArea={drawnArea}
          />
        ) : (
          <QueryPanel
            disabled={!mayRun}
            catalogue={catalogue}
            form={form}
            onChange={setForm}
            onSubmit={submit}
            onSeries={submitSeries}
            onLatest={submitLatest}
            onPreset={applyPreset}
            status={status}
            error={error}
            drawnArea={drawnArea}
            onClearArea={() => setDrawnArea(null)}
          />
        )}
        <a className="ws-switch" href={hrefFor(mode === 'ask' ? 'new' : 'ask')}>
          <Icon name={mode === 'ask' ? 'plus' : 'chat'} size={16} />
          {mode === 'ask' ? 'Prefer a form? Choose everything yourself' : 'Prefer to just ask? Type a question'}
        </a>
      </aside>

      <section className="ws-map">
        <MapView
          result={result}
          selectedZone={selectedZone}
          onSelectZone={setSelectedZone}
          drawnArea={drawnArea}
          onDrawArea={setDrawnArea}
          onUploadBoundary={uploadBoundary}
          boundaryChoices={boundaryChoices}
          onPickBoundary={pickBoundary}
          boundaryMessage={boundaryMessage}
          visible={visible}
          busy={status === 'loading'}
          captureMode={captureMode}
          onCapture={capture}
          onCancelCapture={() => setCaptureMode(null)}
        />
      </section>

      <aside className="ws-result">
        {status === 'loading' && <Progress progress={progress} />}
        {series && !result ? (
          <SeriesPanel
            key={series.points?.map((p) => p.request_id || p.label).join('|')}
            series={series}
            onOpenMonth={openMonth}
            opening={opening}
          />
        ) : (
          <>
            {series && (
              <button type="button" className="back-to-series" onClick={() => { setResult(null); setSelectedZone(null) }}>
                ← Back to the monthly series
              </button>
            )}
            {status !== 'loading' && (
              <ResultPanel
                result={result}
                selectedZone={selectedZone}
                onSelectZone={setSelectedZone}
                highlighted={highlighted}
                onCite={jumpToEvidence}
                onExtend={extendLatest}
                job={job}
                onSave={saveJob}
                onUnsave={unsaveJob}
                pictureTick={pictureTick}
                capturing={captureMode}
                onStartCapture={setCaptureMode}
                onCancelCapture={() => setCaptureMode(null)}
              />
            )}
          </>
        )}
      </aside>
    </div>
  )
}
