import { useEffect, useState } from 'react'

import { api } from '../api'
import { navigate, useNow, useReveal } from '../hooks'
import { EXAMPLE_QUESTIONS } from '../lib/ask'
import { hrefFor } from '../lib/router'
import { firstName } from '../lib/signup'
import { countdown, istTime } from '../lib/when'
import { AnalysisCard, CardSkeleton } from './HistoryPage'
import Icon from './Icon'

const EXAMPLES = [
  { key: 'kerala', title: 'Kerala floods', sub: 'August 2018 · radar', icon: 'water' },
  { key: 'punjab', title: 'Punjab crops', sub: 'Kharif 2023 · vegetation', icon: 'leaf' },
  { key: 'bengaluru', title: 'Bengaluru growth', sub: '2019 to 2023 · built-up', icon: 'building' },
]

/** The satellites, briefly: ESA's next planned image of India and the newest one in Earth Engine. */
function SatelliteCard() {
  const now = useNow(30000)
  const [data, setData] = useState(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    api.satellites().then(setData).catch(() => setFailed(true))
  }, [])
  const next = data?.india?.next_planned?.[0]
  const last = data?.india?.last_image
  return (
    <a className="card sat-card reveal" href={hrefFor('satellites')}>
      <div className="sat-card-head">
        <span className="mini-orbit" aria-hidden="true"><i /><b /></span>
        <div>
          <h3>Satellites</h3>
          <small className="muted">{data ? `${data.satellites.length} Sentinel-1 satellites active` : failed ? 'Not available right now' : 'Checking…'}</small>
        </div>
      </div>
      <dl className="sat-card-figures">
        <div>
          <dt>Next planned image of India</dt>
          <dd>{next ? (next.in_progress ? 'Recording now' : countdown(new Date(next.start) - now)) : '—'}</dd>
          {next && <small>{istTime(next.start)} · {next.satellite.replace('S1', 'Sentinel-1')}</small>}
        </div>
        <div>
          <dt>Newest image to analyse</dt>
          <dd>{last?.time ? countdown(new Date(last.time) - now) : '—'}</dd>
          {last?.time && <small>{istTime(last.time)} · {last.platform}</small>}
        </div>
      </dl>
      <span className="sat-card-link">Open the live satellite view <Icon name="arrow" size={14} /></span>
    </a>
  )
}

function greeting(date = new Date()) {
  const h = date.getHours()
  return h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening'
}

/** The first screen after logging in. */
export default function Home({ user, mayRun }) {
  const [history, setHistory] = useState(null)
  const [usage, setUsage] = useState(null)
  const [question, setQuestion] = useState('')
  const ref = useReveal([history])

  useEffect(() => {
    api.history(6)
      .then((body) => { setHistory(body.items); setUsage(body.usage) })
      .catch(() => setHistory([]))
  }, [])

  const ask = (text) => {
    const q = (text ?? question).trim()
    if (q) navigate(hrefFor('ask', { q }))
  }
  const pct = usage ? Math.min(100, Math.round((usage.used / Math.max(1, usage.limit)) * 100)) : 0

  return (
    <main className="page wrap home" ref={ref}>
      <div className="home-grid">
        <div className="home-main">
          <div className="welcome reveal">
            <h1>{greeting()}, {firstName(user)}</h1>
            <p>Map a flood, ask a question, or open one of your past results.</p>
          </div>

          <a className={`start-card reveal ${mayRun ? '' : 'is-disabled'}`} href={hrefFor('new')}>
            <div className="start-art" aria-hidden="true">
              <span className="ring r1" /><span className="ring r2" /><span className="ring r3" />
              <Icon name="satellite" size={30} />
            </div>
            <div className="start-text">
              <h2>Start a new analysis</h2>
              <p>Choose what to measure, an area and dates. Results in about a minute; ones you ran before open instantly.</p>
            </div>
            <span className="btn btn-primary">Start <Icon name="arrow" size={18} /></span>
          </a>

          <form className="card ask-card reveal" onSubmit={(e) => { e.preventDefault(); ask() }}>
            <div className="ask-card-head"><Icon name="chat" size={20} /><h3>Ask a question</h3></div>
            <div className="ask-row">
              <input value={question} onChange={(e) => setQuestion(e.target.value)} disabled={!mayRun}
                placeholder="How much of Kerala flooded in August 2018?" aria-label="Your question" />
              <button className="btn btn-primary" type="submit" disabled={!mayRun || !question.trim()}>Ask</button>
            </div>
            <div className="chips">
              {EXAMPLE_QUESTIONS.slice(0, 3).map((q) => (
                <button key={q} type="button" className="chip" disabled={!mayRun} onClick={() => ask(q)}>{q}</button>
              ))}
            </div>
          </form>

          <div className="section-row reveal">
            <h3>Recent analyses</h3>
            <a href={hrefFor('history')}>See all <Icon name="arrow" size={14} /></a>
          </div>
          <div className="grid recent">
            {history === null && [0, 1, 2].map((i) => <CardSkeleton key={i} />)}
            {history?.length === 0 && (
              <div className="card empty-state reveal">
                <Icon name="map" size={30} />
                <h3>No analyses yet</h3>
                <p>Try one of the examples, or start your own.</p>
              </div>
            )}
            {history?.map((item, i) => <AnalysisCard key={item.job_id} item={item} style={{ '--delay': `${i * 70}ms` }} />)}
          </div>
        </div>

        <aside className="home-side">
          <div className="card reveal">
            <h3>Your usage</h3>
            <small className="muted">Analyses this hour</small>
            <div className="usage"><i style={{ width: `${pct}%` }} /></div>
            <small>{usage ? <><b>{usage.used}</b> of {usage.limit} used · resets each hour</> : '—'}</small>
          </div>

          <SatelliteCard />

          <div className="card reveal">
            <h3>Try an example</h3>
            <div className="examples">
              {EXAMPLES.map((ex) => (
                <a key={ex.key} className="example" href={hrefFor('new', { example: ex.key })}>
                  <span className="example-icon"><Icon name={ex.icon} size={18} /></span>
                  <span><strong>{ex.title}</strong><small>{ex.sub}</small></span>
                  <Icon name="arrow" size={16} />
                </a>
              ))}
            </div>
          </div>

          <div className="card tip-card reveal">
            <h3>Good to know</h3>
            <p>Radar sees through clouds, but misses water under dense trees and in narrow city streets, so the real flooded area is usually a little larger.</p>
            <a href={hrefFor('help')}>How it works and its limits <Icon name="arrow" size={14} /></a>
          </div>
        </aside>
      </div>
    </main>
  )
}
