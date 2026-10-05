/**
 * Worked examples, runnable in one click from the dashboard or the form.
 * Each is a complete form, so applying one never mixes with what was there.
 */
export const PRESETS = [
  {
    key: 'kerala',
    label: 'Kerala floods, August 2018',
    analysisType: 'flood_extent',
    region: 'kerala',
    when: 'compare',
    postStart: '2018-08-15',
    postEnd: '2018-08-25',
    preStart: '2018-02-01',
    preEnd: '2018-04-30',
  },
  {
    key: 'punjab',
    label: 'Punjab vegetation, kharif',
    analysisType: 'vegetation_health',
    region: 'punjab',
    when: 'one',
    postStart: '2023-09-01',
    postEnd: '2023-09-30',
  },
  {
    key: 'kerala-water',
    label: 'Kerala surface water',
    analysisType: 'water_extent',
    region: 'kerala',
    when: 'one',
    postStart: '2023-01-01',
    postEnd: '2023-03-31',
  },
  {
    key: 'bengaluru',
    label: 'Bengaluru growth, 2019 to 2023',
    analysisType: 'built_up',
    region: 'bangalore urban',
    when: 'compare',
    postStart: '2023-01-01',
    postEnd: '2023-03-31',
    preStart: '2019-01-01',
    preEnd: '2019-03-31',
  },
]

export const findPreset = (key) => PRESETS.find((p) => p.key === key) || null

/**
 * The three ways to ask for dates. 'one' never sends a baseline: a baseline
 * left over from 'compare' and hidden from view was a real bug once.
 */
export const WHEN_MODES = [
  { key: 'one', label: 'One period' },
  { key: 'compare', label: 'Before and after' },
  { key: 'monthly', label: 'Month by month', floodOnly: true },
]

/** The form as it should be sent for its mode. */
export function formForMode(form) {
  if (form.when === 'compare') return form
  return { ...form, preStart: '', preEnd: '' }
}

/** The mode a form is in, for forms saved before modes existed. */
export function modeOf(form) {
  if (form?.when) return form.when
  return form?.preStart ? 'compare' : 'one'
}
