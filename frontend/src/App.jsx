import { useCallback, useEffect, useState } from 'react'

import { api, runAnalysis, runSeries } from './api'
import { describeFootprint } from './lib/bbox'
import AskPanel from './components/AskPanel'
import MapView from './components/MapView'
import QueryPanel from './components/QueryPanel'
import ResultPanel from './components/ResultPanel'
import SeriesPanel from './components/SeriesPanel'
import './App.css'

const DEFAULT_FORM = {
  analysisType: 'flood_extent',
  region: 'kerala',
  postStart: '2018-08-15',
  postEnd: '2018-08-25',
  // No baseline by default. These used to be prefilled, which meant every
  // analysis was a comparison whether or not the user asked for one - and the
  // dates lived inside a collapsed section, so once it was closed the setting
  // was invisible while still being sent. Someone changed the dates, drew an
  // area, and got a change figure against a 2018 baseline they never chose.
  preStart: '',
  preEnd: '',
}

export default function App() {
  const [catalogue, setCatalogue] = useState(null)
  const [form, setForm] = useState(DEFAULT_FORM)
  const [result, setResult] = useState(null)
  const [history, setHistory] = useState([])
  const [status, setStatus] = useState('idle')
  const [error, setError] = useState('')
  const [selectedZone, setSelectedZone] = useState(null)
  const [highlighted, setHighlighted] = useState(null)
  // A shape drawn on the map, or null for a named region:
  //   { kind: 'bbox',   bbox: [west, south, east, north] }
  //   { kind: 'circle', centre: {lat, lng}, radiusKm }
  // Lives here rather than in the form because the map owns the gesture and
  // the query panel owns the name.
  const [drawnArea, setDrawnArea] = useState(null)
  // A monthly series, when one was asked for. Kept alongside `result` rather
  // than replacing it: opening a month puts that month in `result` while the
  // series stays, so "back to the series" costs nothing.
  const [series, setSeries] = useState(null)
  const [opening, setOpening] = useState(false)
  // Kept apart from `error`, which the form panel shows. A refused question
  // carries examples and what was understood, and belongs next to the box it
  // came from; an upload failure belongs next to the upload.
  const [askError, setAskError] = useState(null)
  const [uploadFailure, setUploadFailure] = useState('')

  useEffect(() => {
    api
      .catalogue()
      .then(setCatalogue)
      .catch((err) =>
        setError(
          `Cannot reach the backend at ${api.base}. Is uvicorn running? (${err.message})`,
        ),
      )
  }, [])

  const submit = useCallback(
    async (override) => {
      const query = override || form
      setStatus('loading')
      setError('')
      setSelectedZone(null)

      try {
        const data = await runAnalysis({ ...query, area: drawnArea })
        setSeries(null)
        setResult(data)
        setStatus('done')
        setHistory((current) =>
          [
            {
              id: data.request_id || Date.now(),
              // describeFootprint falls back to the region name, so named
              // regions read as before. Drawn ones get their centre and size,
              // because "user-defined rectangle" is the same string every
              // time and three of them in a row tell you nothing.
              label: `${data.analysis_label || 'Flood extent'} · ${describeFootprint(data.region)}`,
              period: data.period?.post?.start,
              data,
            },
            ...current.filter((h) => h.id !== data.request_id),
          ].slice(0, 8),
        )
      } catch (err) {
        setStatus('error')
        setError(err.message)
      }
    },
    [form, drawnArea],
  )

  // Every single-result path lands the same way, so history, series state
  // and zone selection cannot drift apart between them.
  const showResult = useCallback((data, label) => {
    setSeries(null)
    setResult(data)
    setSelectedZone(null)
    setStatus('done')
    setHistory((current) =>
      [
        {
          id: data.request_id || Date.now(),
          label,
          period: data.period?.post?.start || '',
          data,
        },
        ...current.filter((h) => h.id !== data.request_id),
      ].slice(0, 8),
    )
  }, [])

  const askQuestion = useCallback(async (question) => {
    setStatus('loading')
    setError('')
    setAskError(null)
    try {
      const data = await api.ask({ question, area: drawnArea })
      showResult(data, question.length > 60 ? `${question.slice(0, 57)}…` : question)
    } catch (err) {
      setStatus('error')
      setAskError(err)
    }
  }, [drawnArea, showResult])

  const uploadImage = useCallback(async (file) => {
    setStatus('loading')
    setError('')
    setUploadFailure('')
    try {
      const data = await api.uploadImage(file)
      showResult(data, `Uploaded image · ${file.name}`)
    } catch (err) {
      setStatus('error')
      setUploadFailure(err.message)
    }
  }, [showResult])

  const submitSeries = useCallback(async () => {
    setStatus('loading')
    setError('')
    setSelectedZone(null)
    try {
      const data = await runSeries({ ...form, area: drawnArea })
      setSeries(data)
      setResult(null)
      setStatus('done')
    } catch (err) {
      setStatus('error')
      setError(err.message)
    }
  }, [form, drawnArea])

  // One month of the series, as its own full analysis: evidence, zones, map
  // layers and PDF. Fetched by id from the cache, so nothing is recomputed.
  const openMonth = useCallback(async (requestId) => {
    setOpening(true)
    setError('')
    try {
      const data = await api.analysis(requestId)
      setResult(data)
      setSelectedZone(null)
    } catch (err) {
      setError(err.message)
    } finally {
      setOpening(false)
    }
  }, [])

  const applyPreset = (preset) => {
    const next = { ...DEFAULT_FORM, preStart: '', preEnd: '', ...preset }
    setForm(next)
    submit(next)
  }

  const jumpToEvidence = (id) => {
    setHighlighted(id)
    document.getElementById(`ev-${id}`)?.scrollIntoView({
      behavior: 'smooth',
      block: 'center',
    })
    setTimeout(() => setHighlighted(null), 2000)
  }

  return (
    <div className="app">
      <header className="app-header">
        <div>
          <h1>Antardrishti</h1>
          <p>Earth observation for India, with every number traceable</p>
        </div>
        <div className={`status status-${status}`}>
          {status === 'loading' && 'Running'}
          {status === 'done' && 'Ready'}
          {status === 'error' && 'Error'}
          {status === 'idle' && 'Idle'}
        </div>
      </header>

      <main className="layout">
        <aside className="column left">
          <AskPanel
            onAsk={askQuestion}
            onUpload={uploadImage}
            status={status}
            askError={askError}
            uploadFailure={uploadFailure}
            drawnArea={drawnArea}
          />

          <QueryPanel
            catalogue={catalogue}
            form={form}
            onChange={setForm}
            onSubmit={submit}
            onSeries={submitSeries}
            onPreset={applyPreset}
            status={status}
            error={error}
          />

          {history.length > 0 && (
            <div className="panel history">
              <h3>Recent</h3>
              {history.map((item) => (
                <button
                  key={item.id}
                  className={result?.request_id === item.id ? 'active' : ''}
                  onClick={() => {
                    setResult(item.data)
                    setSelectedZone(null)
                  }}
                >
                  <strong>{item.label}</strong>
                  <span>{item.period}</span>
                </button>
              ))}
            </div>
          )}
        </aside>

        <section className="column centre">
          <MapView
            result={result}
            selectedZone={selectedZone}
            onSelectZone={setSelectedZone}
            drawnArea={drawnArea}
            onDrawArea={setDrawnArea}
          />
        </section>

        <aside className="column right">
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
                <button
                  type="button"
                  className="back-to-series"
                  onClick={() => { setResult(null); setSelectedZone(null) }}
                >
                  ← Back to the monthly series
                </button>
              )}
              <ResultPanel
                result={result}
                selectedZone={selectedZone}
                onSelectZone={setSelectedZone}
                highlighted={highlighted}
                onCite={jumpToEvidence}
              />
            </>
          )}
        </aside>
      </main>
    </div>
  )
}
