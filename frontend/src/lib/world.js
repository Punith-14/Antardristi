/**
 * Country outlines for naming where a satellite is ("over Indonesia").
 * Natural Earth 1:110m via world-atlas (public domain). Used only for names,
 * never drawn - no borders appear anywhere in the app.
 */
import { feature } from 'topojson-client'
import countries110 from 'world-atlas/countries-110m.json'

export const COUNTRIES = feature(countries110, countries110.objects.countries).features

/** India's own outline (from the backend) first, so points in India are named India. */
export function namedAreas(indiaGeometry) {
  return indiaGeometry ? [{ properties: { name: 'India' }, geometry: indiaGeometry }, ...COUNTRIES] : COUNTRIES
}
