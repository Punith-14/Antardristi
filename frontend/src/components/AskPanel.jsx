import { useRef, useState } from 'react'

import {
  EXAMPLE_QUESTIONS,
  askErrorView,
  validateQuestion,
  validateUpload,
} from '../lib/ask'

/**
 * The two ways in that used to be API-only: a question in plain language,
 * and an uploaded image.
 *
 * The question goes to /ask, which works out what to measure, where and when,
 * runs it, and checks that what it measured is what was asked. A shape drawn
 * on the map is sent with the question, so "was there flooding here in
 * August 2018?" works over a district too new to have a name in the 2015
 * boundaries.
 *
 * The upload goes to /analyze-upload. The result is a fraction of the image's
 * pixels, never an area of ground - the panel says so before anyone uploads,
 * so the number does not arrive as a surprise.
 */
export default function AskPanel({ onAsk, onUpload, status, askError, uploadFailure, drawnArea, disabled = false }) {
  const [question, setQuestion] = useState('')
  const [localError, setLocalError] = useState('')
  const [file, setFile] = useState(null)
  const [uploadError, setUploadError] = useState('')
  const fileRef = useRef(null)

  // A viewer account sees the panel but cannot run it (the server refuses too).
  const busy = disabled || (status === 'loading')
  const refused = askErrorView(askError)

  const submitQuestion = (text) => {
    const check = validateQuestion(text ?? question)
    if (!check.ok) {
      setLocalError(check.reason)
      return
    }
    setLocalError('')
    if (text) setQuestion(text)
    onAsk(check.question)
  }

  const submitUpload = () => {
    const check = validateUpload(file)
    if (!check.ok) {
      setUploadError(check.reason)
      return
    }
    setUploadError('')
    onUpload(file)
  }

  return (
    <div className="panel ask-panel">
      <form
        onSubmit={(event) => {
          event.preventDefault()
          submitQuestion()
        }}
      >
        <div className="panel-head">
          <h2>Ask a question</h2>
          <p>
            In plain language. What was understood, and whether it matches what
            you asked, is shown with the answer.
          </p>
        </div>

        <textarea
          className="ask-input"
          rows={3}
          value={question}
          placeholder="How much did flooding increase in Kerala in August 2018 compared to May 2018?"
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={(event) => {
            // Enter asks; Shift+Enter keeps its usual meaning of a new line.
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault()
              submitQuestion()
            }
          }}
          disabled={busy}
          aria-label="Your question"
        />

        {drawnArea && (
          <small className="ask-area-note">
            Your drawn area will be used as the place; the question decides what
            to measure and when.
          </small>
        )}

        <button className="run" type="submit" disabled={busy}>
          {busy ? 'Working…' : 'Ask'}
        </button>

        {localError && <p className="error">{localError}</p>}

        {refused && (
          <div className="ask-refused">
            <p className="error">{refused.message}</p>
            {refused.understood && (
              <p className="ask-understood">
                <span>Understood as</span> {refused.understood}
              </p>
            )}
            {refused.hint && <p className="ask-hint">{refused.hint}</p>}
            {refused.suggestions.length > 0 && (
              <div className="ask-suggestions">
                <span>Try</span>
                {refused.suggestions.map((text) => (
                  <button key={text} type="button" onClick={() => submitQuestion(text)}>
                    {text}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        {!refused && !question && (
          <div className="ask-suggestions">
            <span>For example</span>
            {EXAMPLE_QUESTIONS.map((text) => (
              <button key={text} type="button" disabled={busy}
                onClick={() => submitQuestion(text)}>
                {text}
              </button>
            ))}
          </div>
        )}
      </form>

      <div className="upload-block">
        <h3>Or upload an image</h3>
        <p>
          A photo or screenshot is screened for water-coloured pixels. The
          answer is a share of the image, not an area of ground - a photo has
          no location or scale - and it is not an assessment of flooding.
        </p>
        <input
          ref={fileRef}
          type="file"
          accept=".png,.jpg,.jpeg,.webp"
          onChange={(event) => {
            setFile(event.target.files?.[0] || null)
            setUploadError('')
          }}
          disabled={busy}
          aria-label="Image to screen"
        />
        <button type="button" className="upload-run" disabled={busy || !file}
          onClick={submitUpload}>
          {busy ? 'Working…' : 'Screen this image'}
        </button>
        {(uploadError || uploadFailure) && (
          <p className="error">{uploadError || uploadFailure}</p>
        )}
      </div>
    </div>
  )
}
