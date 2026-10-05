/**
 * Suggestions for the Area box.
 *
 * The box took any text and the name was only checked on Run, so nothing
 * appeared while typing and a name India uses twice (Aurangabad is in Bihar
 * and in Maharashtra) could not be told apart. The list comes from
 * GET /regions/names: every state and district the boundary set knows.
 */

const norm = (text) => (text || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim()

/** What goes in the box when a suggestion is picked. Districts carry their
 *  state, so a repeated name is never ambiguous: "Aurangabad, Bihar". */
export function placeValue(place) {
  if (!place) return ''
  return place.level === 'state' || !place.state ? place.name : `${place.name}, ${place.state}`
}

/** The second line of a suggestion: what kind of place, and where. */
export function placeDetail(place) {
  if (place.level === 'state') return 'State'
  const aka = place.aka ? ` · also written ${titleCase(place.aka)}` : ''
  return `District, ${place.state}${aka}`
}

function titleCase(text) {
  return text.replace(/\b[a-z]/g, (c) => c.toUpperCase())
}

/**
 * Up to `limit` places for what has been typed, best first: names that start
 * with it, then names with a word that starts with it, then anything that
 * contains it. States before districts within each group. Matches the modern
 * spelling (`aka`) too, so "Sivasagar" finds GAUL's "Sibsagar".
 */
export function suggestPlaces(places, typed, limit = 8) {
  const q = norm(typed)
  if (q.length < 2 || !Array.isArray(places)) return []
  if (places.some((p) => norm(placeValue(p)) === q)) return []   // already picked
  const scored = []
  for (const place of places) {
    const names = [norm(place.name), norm(place.aka)].filter(Boolean)
    let rank = null
    for (const name of names) {
      const r = name.startsWith(q) ? 0
        : name.split(' ').some((w) => w.startsWith(q)) ? 1
          : name.includes(q) ? 2 : null
      if (r !== null && (rank === null || r < rank)) rank = r
    }
    if (rank !== null) scored.push({ place, rank })
  }
  scored.sort((a, b) =>
    a.rank - b.rank
    || (a.place.level === 'state' ? 0 : 1) - (b.place.level === 'state' ? 0 : 1)
    || a.place.name.localeCompare(b.place.name))
  return scored.slice(0, limit).map((s) => s.place)
}
