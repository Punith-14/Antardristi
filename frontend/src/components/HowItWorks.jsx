import { useState } from 'react'

import { accuracyRows } from '../lib/accuracy'
import { LIMITS, STEPS, searchGlossary } from '../lib/glossary'
import Term from './Term'

/**
 * How the platform works, what its numbers mean, and what it cannot do.
 *
 * The accuracy table is read from /analyses - the same constants the
 * detectors use - so this page cannot quote a stale figure.
 */
export default function HowItWorks({ catalogue, onClose }) {
  const [query, setQuery] = useState('')
  const rows = accuracyRows(catalogue)
  const terms = searchGlossary(query)

  return (
    <aside className="how-it-works" role="dialog" aria-label="How it works">
      <div className="hiw-head">
        <h2>How it works</h2>
        <button type="button" onClick={onClose} aria-label="Close">×</button>
      </div>

      <section>
        <h3>From satellite to answer</h3>
        <ol className="hiw-steps">
          {STEPS.map((step) => (
            <li key={step.title}><strong>{step.title}.</strong> {step.text}</li>
          ))}
        </ol>
      </section>

      <section>
        <h3>How accurate is it?</h3>
        <p className="hiw-small">
          Scored against hand-mapped floods the methods were not tuned on.{' '}
          <Term k="iou">IoU</Term>, <Term k="precision">precision</Term> and{' '}
          <Term k="recall">recall</Term> run from 0 to 1; higher is better.
        </p>
        {rows.length ? (
          <table className="hiw-table">
            <thead>
              <tr><th>Method</th><th>IoU</th><th>Precision</th><th>Recall</th><th>Measured at</th></tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.key}>
                  <td>{r.label}{r.note && <small>{r.note}</small>}</td>
                  <td>{r.iou}</td><td>{r.precision}</td><td>{r.recall}</td><td>{r.scale}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="hiw-small">Accuracy figures load from the backend; it is not reachable right now.</p>
        )}
      </section>

      <section>
        <h3>What it cannot do</h3>
        <ul className="hiw-limits">
          {LIMITS.map((limit) => (
            <li key={limit.title}>
              <strong>{limit.title}.</strong> {limit.text} <small>Source: {limit.source}.</small>
            </li>
          ))}
        </ul>
      </section>

      <section>
        <h3>Glossary</h3>
        <input
          className="hiw-search" value={query} placeholder="Search terms…"
          onChange={(event) => setQuery(event.target.value)} aria-label="Search the glossary"
        />
        <dl className="hiw-glossary">
          {terms.map((t) => (
            <div key={t.key} id={`term-${t.key}`}>
              <dt>{t.term} <span lang="hi">{t.hindi}</span></dt>
              <dd>{t.short} <em>{t.why}</em></dd>
            </div>
          ))}
        </dl>
      </section>
    </aside>
  )
}
