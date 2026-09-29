import { useCallback, useEffect, useState } from 'react'

import { api, runAnalysis } from './api'
import { describeFootprint } from './lib/bbox'
import MapView from './components/MapView'
import QueryPanel from './components/QueryPanel'
import ResultPanel from './components/ResultPanel'
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
          <QueryPanel
            catalogue={catalogue}
            form={form}
            onChange={setForm}
            onSubmit={submit}
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
          <ResultPanel
            result={result}
            selectedZone={selectedZone}
            onSelectZone={setSelectedZone}
            highlighted={highlighted}
            onCite={jumpToEvidence}
          />
        </aside>
      </main>
    </div>
  )
}
