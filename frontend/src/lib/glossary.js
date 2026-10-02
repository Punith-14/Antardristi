/**
 * Plain-language definitions of every technical term the interface shows,
 * the limits of the platform, and the fixed Hindi for each term.
 *
 * Two rules, both held by tests:
 *   1. No accuracy numbers here. Figures come from /analyses at run time,
 *      read from the detectors' own constants; a number typed into a glossary
 *      goes stale the day a threshold changes.
 *   2. Every term the interface marks with <Term k="..."> has an entry.
 *
 * The Hindi column is the fixed term list the Hindi report translation is
 * checked against (D4): one word per idea, used the same way every time.
 */

export const GLOSSARY = {
  flood_extent: {
    term: 'Flood extent',
    hindi: 'बाढ़ का विस्तार',
    short: 'Area the radar found covered by water that is not normally there, in km².',
    why: 'The headline figure. Rivers and lakes present year-round are removed first, so this is new water.',
  },
  observable_area: {
    term: 'Observable area',
    hindi: 'अवलोकित क्षेत्र',
    short: 'The part of the region the satellite actually saw in the chosen dates.',
    why: 'Percentages are of this, not of the whole region. Ground that was not seen is never counted as dry.',
  },
  coverage: {
    term: 'Coverage',
    hindi: 'कवरेज',
    short: 'Observable area as a share of the whole region.',
    why: 'One radar pass often covers only part of a state. Low coverage means part of the region is simply unknown.',
  },
  permanent_water: {
    term: 'Permanent water',
    hindi: 'स्थायी जल',
    short: 'Rivers, lakes and reservoirs that hold water at least 90% of the time (JRC Global Surface Water).',
    why: 'Excluded from the flood extent, so a river is not reported as a flood.',
  },
  baseline: {
    term: 'Baseline',
    hindi: 'आधार अवधि',
    short: 'An earlier, drier period measured the same way, to compare against.',
    why: 'Net new water = flood extent minus baseline water. Without one, you get extent, not change.',
  },
  backscatter: {
    term: 'Backscatter',
    hindi: 'बैकस्कैटर',
    short: 'How much of the radar pulse bounces back to the satellite.',
    why: 'Calm water reflects the pulse away, so it looks dark. That darkness is how water is found.',
  },
  db: {
    term: 'dB (decibels)',
    hindi: 'डेसिबल',
    short: 'The scale backscatter is measured on. More negative means darker.',
    why: 'The water threshold is a dB value: darker than it is called water.',
  },
  threshold: {
    term: 'Threshold',
    hindi: 'सीमा मान',
    short: 'The cut-off value separating water from land.',
    why: 'Chosen by measurement on hand-labelled floods, not by guesswork, and different at each analysis scale.',
  },
  sentinel_1: {
    term: 'Sentinel-1 (radar)',
    hindi: 'सेंटिनल-1 (रडार)',
    short: 'European radar satellites that see through cloud, by day and night.',
    why: 'The default flood sensor: monsoon floods happen under cloud.',
  },
  sentinel_2: {
    term: 'Sentinel-2 (optical)',
    hindi: 'सेंटिनल-2 (ऑप्टिकल)',
    short: 'European satellites that photograph the ground in visible and infrared light.',
    why: 'Sharper than radar, but blind under cloud - most of a monsoon flood may be unobserved.',
  },
  mndwi: {
    term: 'MNDWI',
    hindi: 'एमएनडीडब्ल्यूआई जल सूचकांक',
    short: 'A water index from optical bands: high where there is open water.',
    why: 'The optical flood rule thresholds it.',
  },
  iou: {
    term: 'IoU',
    hindi: 'आईओयू (मिलान स्कोर)',
    short: 'Overlap between the map and hand-drawn flood labels, from 0 (none) to 1 (perfect).',
    why: 'One number for overall map quality. It punishes both false water and missed water.',
  },
  precision: {
    term: 'Precision',
    hindi: 'परिशुद्धता',
    short: 'Of the pixels the map calls water, the share that really are water.',
    why: 'Low precision means false alarms.',
  },
  recall: {
    term: 'Recall',
    hindi: 'रिकॉल (पकड़ दर)',
    short: 'Of the real flood water, the share the map found.',
    why: 'Low recall means missed flooding - and an undercount of people affected.',
  },
  validation: {
    term: 'Validation',
    hindi: 'सत्यापन',
    short: 'Scoring the method against hand-labelled floods it was not tuned on (Sen1Floods11).',
    why: 'Every accuracy figure shown comes from this, at the scale the service runs at.',
  },
  confidence_range: {
    term: '95% range',
    hindi: '95% विश्वास सीमा',
    short: 'The spread an accuracy figure could plausibly take, from resampling the test floods.',
    why: 'With few test floods - one Indian event - the range is the honest answer, not the single number.',
  },
  zone: {
    term: 'Zone',
    hindi: 'क्षेत्र खंड',
    short: 'A connected patch of flooding, ranked largest first.',
    why: 'Turns a raster into places a team can be sent to.',
  },
  severity: {
    term: 'Severity',
    hindi: 'गंभीरता',
    short: 'How much of a zone is flooded: high, moderate or low.',
    why: 'A quick way to order zones; it is about extent, not depth or damage.',
  },
  population_model: {
    term: 'Population model',
    hindi: 'जनसंख्या मॉडल',
    short: 'Gridded estimates of where people live (GHSL and WorldPop), not a census count.',
    why: 'People in the flooded area are shown as a range between the two models.',
  },
  evidence_record: {
    term: 'Evidence record',
    hindi: 'साक्ष्य रिकॉर्ड',
    short: 'The list of every computed figure, with the method and data source behind it.',
    why: 'Each [E1]-style citation in the report points to a row here.',
  },
  verified: {
    term: 'Verified',
    hindi: 'सत्यापित',
    short: 'Every number in the report was checked against the evidence record, and every caveat kept.',
    why: 'A report that fails is labelled, never shown as verified.',
  },
  scene: {
    term: 'Scene',
    hindi: 'उपग्रह दृश्य',
    short: 'One satellite image, with its own ID and acquisition time.',
    why: 'The scene list shows exactly which images a result came from.',
  },
  relative_orbit: {
    term: 'Relative orbit',
    hindi: 'सापेक्ष कक्षा',
    short: 'Which of the satellite\'s repeating tracks took the image.',
    why: 'Comparisons use one orbit, so a difference is water, not viewing angle.',
  },
  hand: {
    term: 'HAND (height above drainage)',
    hindi: 'निकटतम जलनिकासी से ऊँचाई',
    short: 'How many metres a place sits above the stream it drains into.',
    why: 'Floods cannot sit high above drainage. A HAND check was measured here and NOT adopted: it removed real Assam floodplain water.',
  },
  analysis_scale: {
    term: 'Analysis scale',
    hindi: 'विश्लेषण पैमाना',
    short: 'The pixel size areas are measured at, in metres (usually 200 m).',
    why: 'Thresholds are measured separately at each scale; small features below it are not resolved.',
  },
  latest_pass: {
    term: 'Latest pass',
    hindi: 'नवीनतम उपग्रह गुज़र',
    short: 'The most recent Sentinel-1 image over the area.',
    why: '"Now" means the newest image, and the result says how old it is.',
  },
  boundary_2015: {
    term: '2015 boundaries',
    hindi: '2015 की सीमाएँ',
    short: 'Named regions come from FAO GAUL 2015; a name it lacks (Telangana, a few newer districts) is looked up in GAUL 2025, and the result says so.',
    why: 'Most districts created after 2015 are in neither set; draw or upload their boundary instead.',
  },
}

/** Quantities in the evidence record, mapped to the term that explains them. */
export const QUANTITY_TERMS = {
  flood_extent: 'flood_extent',
  flood_extent_fraction: 'observable_area',
  permanent_water_area: 'permanent_water',
  baseline_water_extent: 'baseline',
  net_new_water: 'baseline',
  flood_extent_in_common_area: 'observable_area',
  zone_count: 'zone',
  largest_zone_area: 'zone',
  zoned_fraction: 'zone',
  water_excluded_by_terrain: 'hand',
  population_in_flood_extent_ghsl: 'population_model',
  population_in_flood_extent_worldpop: 'population_model',
}

export function termFor(key) {
  return GLOSSARY[key] || null
}

export function termForQuantity(quantity) {
  return termFor(QUANTITY_TERMS[quantity])
}

/** The tooltip text for a term: definition, then why it matters. */
export function tooltip(key) {
  const entry = termFor(key)
  return entry ? `${entry.short} ${entry.why}` : ''
}

/** Glossary entries filtered by a search string, alphabetical. */
export function searchGlossary(query = '') {
  const q = query.trim().toLowerCase()
  return Object.entries(GLOSSARY)
    .filter(([, e]) => !q || e.term.toLowerCase().includes(q) || e.short.toLowerCase().includes(q) ||
      e.hindi.includes(query.trim()))
    .map(([key, e]) => ({ key, ...e }))
    .sort((a, b) => a.term.localeCompare(b.term))
}

/** The fixed English -> Hindi term list, for checking a translation. */
export function hindiTerms() {
  return Object.fromEntries(Object.values(GLOSSARY).map((e) => [e.term, e.hindi]))
}

/**
 * What the platform cannot do. Each names where it was measured, so a reader
 * can check; none quotes a figure - those come from /analyses.
 */
export const LIMITS = [
  {
    title: 'Water under trees and tall crops is often missed',
    text: 'Radar bounces off vegetation and back to the satellite, so flooded forest and crops look bright, not dark.',
    source: 'Notebook 04, missed-water analysis',
  },
  {
    title: 'Dry, smooth ground can look like water',
    text: 'Sand, hardpan and tarmac are dark to radar. Precision is far lower over arid ground than in Assam; treat maps of Rajasthan or Kutch with caution.',
    source: 'Notebook 08, per-event accuracy',
  },
  {
    title: 'Some flood water is always missed',
    text: 'Turbid, shallow or wind-roughened water can be too bright for any defensible threshold, so extent and people counts lean low.',
    source: 'Notebooks 04 and 07',
  },
  {
    title: 'Accuracy in India rests on one flood event',
    text: 'The only Indian test flood is Assam, 2016. Coastal, Himalayan and urban floods are not measured; the 95% range is shown with the figure.',
    source: 'Notebook 08',
  },
  {
    title: 'District names are mostly from 2015',
    text: 'Telangana and a handful of newer districts come from GAUL 2025 instead, labelled; most districts created since 2015 are in neither set. Draw or upload the boundary instead.',
    source: 'FAO GAUL 2015 and 2025, compared by scripts/compare_boundaries.py',
  },
  {
    title: 'People figures are model estimates',
    text: 'Residents of flooded grid cells from two population models, shown as a range - not people displaced, injured or counted on the ground.',
    source: 'GHSL and WorldPop',
  },
  {
    title: 'Optical cannot see through cloud',
    text: 'During the monsoon most of a flood may be unobserved by Sentinel-2; unobserved ground is reported, never assumed dry.',
    source: 'Kerala 2019 coverage measurement',
  },
  {
    title: 'A terrain check was tried and not adopted',
    text: 'Removing water high above drainage cut false alarms elsewhere but deleted real Assam floodplain water, so it is off.',
    source: 'Notebook 09',
  },
]

/** The pipeline in five steps, for the How it works panel. */
export const STEPS = [
  { title: 'The satellite images the ground', text: 'Sentinel-1 radar passes over every few days and sees through cloud, day or night.' },
  { title: 'Water shows up dark', text: 'Calm water reflects the radar pulse away from the satellite, so flooded ground is darker than dry ground.' },
  { title: 'A measured threshold picks out water', text: 'Pixels darker than a threshold - chosen by testing against hand-mapped floods at the scale the service runs at - are called water.' },
  { title: 'Normal water is removed', text: 'Rivers and lakes present year-round are subtracted, and ground the satellite did not see is reported as unobserved, never as dry.' },
  { title: 'Every number is checked', text: 'Each figure goes into an evidence record. The written report is checked against it: every number must trace to a row, and every caveat must be kept.' },
]
