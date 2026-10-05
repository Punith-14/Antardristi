import { useEffect, useMemo, useState } from 'react'

import { api } from '../api'
import { placeDetail, placeValue, suggestPlaces } from '../lib/placesearch'

// Loaded once per page visit and shared by every Area box.
let cached = null

/** The Area box: type a name, pick a state or district from the list. */
export default function PlaceInput({ value, onChange, disabled }) {
  const [places, setPlaces] = useState(cached)
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)

  useEffect(() => {
    if (cached) return
    let alive = true
    api.regionNames()
      .then((body) => { cached = body.places || []; if (alive) setPlaces(cached) })
      .catch(() => { /* the box still works without suggestions */ })
    return () => { alive = false }
  }, [])

  const matches = useMemo(() => suggestPlaces(places, value), [places, value])
  const showing = open && matches.length > 0

  const pick = (place) => {
    onChange(placeValue(place))
    setOpen(false)
  }

  const onKeyDown = (event) => {
    if (!showing) return
    if (event.key === 'ArrowDown') { event.preventDefault(); setActive((a) => Math.min(a + 1, matches.length - 1)) }
    else if (event.key === 'ArrowUp') { event.preventDefault(); setActive((a) => Math.max(a - 1, 0)) }
    else if (event.key === 'Enter') { event.preventDefault(); pick(matches[active]) }
    else if (event.key === 'Escape') setOpen(false)
  }

  return (
    <div className="place-input">
      <input value={value} disabled={disabled} aria-label="State or district" autoComplete="off"
        role="combobox" aria-expanded={showing} aria-controls="place-list"
        placeholder="Kerala, Morigaon, Darbhanga…"
        onChange={(e) => { onChange(e.target.value); setOpen(true); setActive(0) }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 120)}
        onKeyDown={onKeyDown} />
      {showing && (
        <ul className="place-list" id="place-list" role="listbox">
          {matches.map((place, i) => (
            <li key={`${place.level}-${place.name}-${place.state}`} role="option" aria-selected={i === active}
              className={i === active ? 'on' : ''}
              onMouseDown={(e) => { e.preventDefault(); pick(place) }}
              onMouseEnter={() => setActive(i)}>
              <strong>{place.name}</strong>
              <small>{placeDetail(place)}</small>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
