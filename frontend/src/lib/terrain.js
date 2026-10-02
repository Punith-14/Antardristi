/**
 * The terrain check (Group C) as the user sees it.
 *
 * Only exists once notebook 09 has measured it and it passed; until then
 * the catalogue's `flood.terrain` is null and nothing here shows.
 */

/** Can the chosen sensor and method use the check? Mirrors analysis.terrain_status. */
export function terrainAvailable(catalogue, option) {
  return Boolean(catalogue?.flood?.terrain) &&
    option?.sensor === 'sentinel-1' && option?.method === 'threshold'
}

/** The line under a result, or null when there is no terrain rule at all. */
export function terrainLine(terrain) {
  if (!terrain) return null
  if (!terrain.applied) return `Terrain check not applied: ${terrain.reason}.`
  const parts = []
  if (terrain.max_hand_m != null) parts.push(`more than ${terrain.max_hand_m} m above the nearest drainage`)
  if (terrain.max_slope_deg != null) parts.push(`on slopes steeper than ${terrain.max_slope_deg}°`)
  const km2 = terrain.excluded_km2 == null ? 'Dark ground' : `${terrain.excluded_km2.toLocaleString()} km² of dark ground`
  return `Terrain check on: ${km2} ${parts.join(' or ')} was not counted as flood (shown in magenta on the map).`
}

/**
 * The option as it will actually run. With the check on, the accuracy shown
 * when choosing must be the accuracy measured WITH the check - the figure the
 * result will quote - not the plain threshold's.
 */
export function effectiveOption(option, catalogue, form) {
  if (!terrainAvailable(catalogue, option) || form?.terrainCheck === false) return option
  const terrain = catalogue.flood.terrain
  return {
    ...option,
    validation: terrain.validation || option.validation,
    measured_at_m: 200,
    rule: `${option.rule}; dark ground ${terrain.text} not counted`,
  }
}
