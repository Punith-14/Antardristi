import { useEffect, useState } from 'react'

import { api, pdfUrl, pictureUrl } from '../api'
import { toast } from '../hooks'
import Icon from './Icon'

/** One picture, with a placeholder while Earth Engine draws it. */
function Picture({ item, onOpen }) {
  const [state, setState] = useState('loading')
  const src = pictureUrl(item.url)
  return (
    <figure className={`pic pic-${state}`}>
      <button type="button" className="pic-frame" onClick={() => state === 'ready' && onOpen(src)}
        aria-label={`Open ${item.label} full size`}>
        {state === 'loading' && (
          <span className="pic-wait"><span className="spinner dark" />{item.ready ? 'Loading…' : 'Drawing from the analysis…'}</span>
        )}
        {state === 'error' && <span className="pic-wait">Could not draw this picture. Try again later.</span>}
        <img src={src} alt={item.label} loading="lazy" onLoad={() => setState('ready')} onError={() => setState('error')} />
      </button>
      <figcaption>{item.label}</figcaption>
    </figure>
  )
}

/**
 * The report's pictures: the automatic ones (drawn from the analysis the
 * first time they are shown) and the ones the user captures by drawing on
 * the map. Every one goes into the PDF.
 */
export default function PicturesTab({ result, job, tick, capturing, onStartCapture, onCancelCapture }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [zoom, setZoom] = useState(null)
  const requestId = result.request_id

  useEffect(() => {
    let alive = true
    api.pictures(requestId)
      .then((body) => { if (alive) { setData(body); setError('') } })
      .catch((err) => { if (alive) setError(err.message) })
    return () => { alive = false }
  }, [requestId, tick])

  const captures = data?.captures || []
  const full = captures.length >= (data?.max_captures || 6)

  const editCaption = async (id, caption) => {
    try {
      await api.editPicture(id, { caption })
    } catch (err) {
      toast(err.message, 'error')
    }
  }
  const remove = async (id) => {
    try {
      await api.deletePicture(id)
      setData((d) => ({ ...d, captures: d.captures.filter((c) => c.id !== id) }))
    } catch (err) {
      toast(err.message, 'error')
    }
  }
  const move = async (index, step) => {
    const next = [...captures]
    const other = index + step
    if (other < 0 || other >= next.length) return
    ;[next[index], next[other]] = [next[other], next[index]]
    setData((d) => ({ ...d, captures: next }))
    await Promise.all(next.map((c, i) => api.editPicture(c.id, { position: i }).catch(() => {})))
  }

  if (error) return <p className="error">{error}</p>
  if (!data) return <p className="muted"><span className="spinner dark" /> Loading pictures…</p>

  return (
    <div className="pictures-tab">
      <div className="capture-box">
        <div>
          <strong><Icon name="camera" size={16} /> Capture for report</strong>
          <small>Draw around a village, a road or a river bend; it is added to the report with your caption.</small>
        </div>
        {capturing ? (
          <button type="button" className="btn btn-outline btn-sm" onClick={onCancelCapture}>Cancel</button>
        ) : (
          <div className="capture-buttons">
            <button type="button" className="btn btn-primary btn-sm" disabled={full} onClick={() => onStartCapture('circle')}>
              <Icon name="circle" size={15} /> Circle
            </button>
            <button type="button" className="btn btn-outline btn-sm" disabled={full} onClick={() => onStartCapture('box')}>
              <Icon name="square" size={15} /> Box
            </button>
          </div>
        )}
      </div>
      {full && <small className="muted">Up to {data.max_captures} captured pictures per report. Remove one to add another.</small>}

      {captures.length > 0 && <h4 className="pic-head">Your captured pictures</h4>}
      <ol className="capture-list">
        {captures.map((c, i) => (
          <li key={c.id}>
            <Picture item={{ ...c, label: c.caption || 'Captured view', ready: true }} onOpen={setZoom} />
            <div className="capture-edit">
              <input defaultValue={c.caption || ''} placeholder="Caption, e.g. Mayong village, road cut off"
                maxLength={200} onBlur={(e) => e.target.value !== (c.caption || '') && editCaption(c.id, e.target.value)}
                aria-label="Caption" />
              <div className="capture-actions">
                <button type="button" onClick={() => move(i, -1)} disabled={i === 0} aria-label="Move up"><Icon name="up" size={15} /></button>
                <button type="button" onClick={() => move(i, 1)} disabled={i === captures.length - 1} aria-label="Move down"><Icon name="down" size={15} /></button>
                <button type="button" onClick={() => remove(c.id)} aria-label="Remove"><Icon name="trash" size={15} /></button>
              </div>
            </div>
          </li>
        ))}
      </ol>

      <h4 className="pic-head">Included automatically</h4>
      <div className="pic-grid">
        {data.auto.map((item) => <Picture key={item.id} item={item} onOpen={setZoom} />)}
      </div>

      <div className="pic-foot">
        {pdfUrl(result) && <a className="btn btn-outline btn-sm" href={pdfUrl(result)} download><Icon name="download" size={15} /> PDF with these pictures</a>}
        <small className="muted">
          {job?.saved ? 'Saved with this analysis.' : 'Kept with the analysis when you save it; otherwise removed with it after a week.'}
        </small>
      </div>

      {zoom && (
        <div className="lightbox" role="dialog" aria-label="Picture" onClick={() => setZoom(null)}>
          <img src={zoom} alt="" />
          <button type="button" aria-label="Close"><Icon name="close" size={22} /></button>
        </div>
      )}
    </div>
  )
}
