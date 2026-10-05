import { useEffect, useMemo, useState } from 'react'

import { api, pdfUrl } from '../api'
import { toast, useReveal } from '../hooks'
import { hrefFor } from '../lib/router'
import { headlineText, keepText, matchesFilter, openHref, titleOf } from '../lib/historyview'
import { peopleRange, periodText, relativeTime } from '../lib/summary'
import Icon from './Icon'


/** A little picture for a card: a river and flood shapes seeded by the job id. */
function Thumb({ seed = '' }) {
  const n = [...String(seed)].reduce((a, c) => (a * 31 + c.charCodeAt(0)) % 997, 7)
  const y = 40 + (n % 30)
  const x = 40 + (n % 160)
  return (
    <svg className="thumb" viewBox="0 0 300 100" preserveAspectRatio="none" aria-hidden="true">
      <rect width="300" height="100" fill="#EAF0F5" />
      <path d={`M0 ${y} C80 ${y - 25} 160 ${y + 25} 300 ${y - 8}`} stroke="#2C4250" strokeWidth="8" fill="none" />
      <ellipse className="thumb-flood" cx={x + 40} cy={y + 4} rx={34 + (n % 18)} ry={14 + (n % 8)} fill="#00B7FF" />
      <ellipse className="thumb-flood" cx={(x + 150) % 280} cy={y - 6} rx={18 + (n % 10)} ry={9} fill="#00B7FF" opacity=".8" />
    </svg>
  )
}

/** One past analysis as a card (Home dashboard). */
export function AnalysisCard({ item, style }) {
  const people = peopleRange(item.people)
  return (
    <a className="card analysis-card reveal" href={openHref(item)} style={style}>
      <Thumb seed={item.job_id} />
      <div className="analysis-body">
        <span className="kind-pill">{item.kind === 'ask' ? 'Question' : item.analysis || item.kind_label}</span>
        {item.saved && <span className="saved-mark" title="Saved"><Icon name="bookmark" size={14} /></span>}
        <strong className="analysis-title">{titleOf(item)}</strong>
        <small>{periodText(item.period) || '—'} · {relativeTime(item.finished_at || item.created_at)}</small>
        <div className="kv">
          {headlineText(item) && <div><b>{headlineText(item)}</b>{item.headline.label.toLowerCase()}</div>}
          {people && <div><b>{people}</b>people</div>}
          {item.months && <div><b>{item.months}</b>months</div>}
        </div>
      </div>
    </a>
  )
}

export function CardSkeleton() {
  return (
    <div className="card analysis-card skeleton" aria-hidden="true">
      <div className="thumb sk" />
      <div className="analysis-body"><i className="sk sk-line" /><i className="sk sk-line short" /><i className="sk sk-line" /></div>
    </div>
  )
}

const FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'saved', label: 'Saved' },
  { key: 'flood', label: 'Floods' },
  { key: 'other', label: 'Other analyses' },
  { key: 'ask', label: 'Questions' },
  { key: 'series', label: 'Monthly' },
]

/** Every finished analysis of yours, searchable, newest first. */
export default function HistoryPage() {
  const [items, setItems] = useState(null)
  const [error, setError] = useState('')
  const [filter, setFilter] = useState('all')
  const [query, setQuery] = useState('')
  const ref = useReveal([items])

  useEffect(() => {
    api.history(200).then((body) => setItems(body.items)).catch((err) => setError(err.message))
  }, [])

  const shown = useMemo(() => (items || []).filter((i) => matchesFilter(i, filter, query)), [items, filter, query])

  const replace = (row) => setItems((list) => list.map((i) => (i.job_id === row.job_id ? { ...i, ...row } : i)))
  const toggleSave = async (item) => {
    try {
      replace(item.saved ? await api.unsaveJob(item.job_id) : await api.saveJob(item.job_id, titleOf(item), item.note))
      toast(item.saved ? 'No longer saved: removed after a week.' : 'Saved.', 'ok')
    } catch (err) {
      toast(err.message, 'error')
    }
  }
  const remove = async (item) => {
    if (!window.confirm(`Delete "${titleOf(item)}"? This cannot be undone.`)) return
    try {
      await api.deleteJob(item.job_id)
      setItems((list) => list.filter((i) => i.job_id !== item.job_id))
      toast('Deleted.', 'ok')
    } catch (err) {
      toast(err.message, 'error')
    }
  }

  return (
    <main className="page wrap" ref={ref}>
      <div className="page-head">
        <div>
          <h1>History</h1>
          <p>Every analysis you've run. Unsaved ones are kept for a week; save the ones you want to keep.
            Opening one shows it again instantly; nothing is recomputed.</p>
        </div>
        <div className="search-box">
          <Icon name="search" size={18} />
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search by place or question" aria-label="Search history" />
        </div>
      </div>

      <div className="filter-row" role="tablist">
        {FILTERS.map((f) => (
          <button key={f.key} type="button" role="tab" aria-selected={filter === f.key}
            className={filter === f.key ? 'on' : ''} onClick={() => setFilter(f.key)}>{f.label}</button>
        ))}
      </div>

      {error && <p className="error">{error}</p>}

      <div className="card table-card">
        {items === null && !error ? (
          <div className="table-loading"><span className="spinner dark" /> Loading your analyses…</div>
        ) : shown.length === 0 ? (
          <div className="empty-state">
            <Icon name="history" size={32} />
            <h3>{items?.length ? 'Nothing matches' : 'Start your first analysis'}</h3>
            <p>{items?.length ? 'Try another filter or search.' : 'Results you run appear here, so you can open them again any time.'}</p>
            {!items?.length && <a className="btn btn-primary" href={hrefFor('new')}>New analysis</a>}
          </div>
        ) : (
          <table className="history-table">
            <thead>
              <tr><th>What</th><th>Where</th><th>Dates</th><th>Result</th><th>People</th><th>Run</th><th>Kept</th><th /></tr>
            </thead>
            <tbody>
              {shown.map((item, i) => {
                const pdf = item.request_id ? pdfUrl({ request_id: item.request_id, ask_id: item.ask_id }) : null
                return (
                  <tr key={item.job_id} className="reveal" style={{ '--delay': `${Math.min(i, 10) * 40}ms` }}>
                    <td><span className="kind-pill">{item.kind === 'ask' ? 'Question' : item.analysis || item.kind_label}</span></td>
                    <td className="history-where">
                      {titleOf(item)}
                      {item.kind === 'ask' && item.place && <small>{item.place}</small>}
                      {item.saved && item.note && <small className="history-note">{item.note}</small>}
                    </td>
                    <td>{periodText(item.period) || '—'}</td>
                    <td className="num">{headlineText(item) || '—'}</td>
                    <td className="num">{peopleRange(item.people) || '—'}</td>
                    <td><small>{relativeTime(item.finished_at || item.created_at)}</small></td>
                    <td><span className={`keep ${item.saved ? 'keep-saved' : ''}`}>{keepText(item)}</span></td>
                    <td className="row-actions">
                      <a className="btn btn-outline btn-sm" href={openHref(item)}>Open</a>
                      {pdf && <a className="btn btn-link btn-sm" href={pdf} download>PDF</a>}
                      <button type="button" className={`icon-btn ${item.saved ? 'on' : ''}`} onClick={() => toggleSave(item)}
                        title={item.saved ? 'Unsave' : 'Save'} aria-label={item.saved ? 'Unsave' : 'Save'}>
                        <Icon name="bookmark" size={16} />
                      </button>
                      <button type="button" className="icon-btn danger" onClick={() => remove(item)} title="Delete" aria-label="Delete">
                        <Icon name="trash" size={16} />
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>
    </main>
  )
}
