import { Suspense, lazy, useEffect } from 'react'

import { useCountTo, useInView, useReveal } from '../hooks'
import { hrefFor } from '../lib/router'
import { reliabilityView } from '../lib/reliability'
import HeroDemo from './HeroDemo'
import Icon from './Icon'
import SiteHeader, { Logo } from './SiteHeader'

// Three.js and the orbit model load only when the visitor nears this section.
const LandingLive = lazy(() => import('./LandingLive'))

const SERVICES = [
  { icon: 'radar', title: 'Radar flood mapping',
    text: 'Sentinel-1 radar sees water through monsoon cloud, day or night. Any state, district, drawn area or uploaded boundary.' },
  { icon: 'people', title: 'People and districts affected',
    text: 'People living in the flooded area from two population models, a district-by-district breakdown, and the villages and roads inside flood zones.' },
  { icon: 'shield', title: 'Reports checked against the data',
    text: 'An AI writes the summary; every number in it is then traced back to the measurement before you see it. Unsupported figures are flagged.' },
  { icon: 'download', title: 'GIS downloads',
    text: 'GeoTIFF, GeoJSON, KML and CSV that open straight in QGIS, ArcGIS or Google Earth, each stamped with its request ID.' },
  { icon: 'language', title: 'Reports in Hindi',
    text: 'The verified English report translated to Hindi, with every number and place name checked again after translation.' },
  { icon: 'chat', title: 'Ask in plain language',
    text: '"How much of Kerala flooded in August 2018?" The answer shows what was understood and the figures behind it.' },
]

// Overlap with expert maps, from the backend's own validation constants
// (detection/sar.py and detection/surface.py). Flood: held-out test, 200 m.
const ANALYSES = [
  { name: 'Flood extent', iou: 0.61 },
  { name: 'Vegetation health', grade: 'good', iou: 0.888 },
  { name: 'Bare ground', grade: 'good', iou: 0.747 },
  { name: 'Surface water', grade: 'moderate', iou: 0.466 },
  { name: 'Built-up area', grade: 'moderate', iou: 0.433 },
  { name: 'Green cover', grade: 'poor', iou: 0.405 },
  { name: 'Crop and vegetation stress', grade: 'unvalidated' },
]

const STEPS = [
  { icon: 'map', title: 'Choose the area and dates',
    text: 'Search a state or district, draw on the map, or upload your own boundary file.' },
  { icon: 'satellite', title: 'Satellites measure the water',
    text: 'Radar images from those dates are read, and permanent rivers and lakes are separated from new flood water.' },
  { icon: 'check', title: 'Get the map, report and files',
    text: 'A flood map, people affected, a checked report in English or Hindi, and files for your GIS team.' },
]

const WHO = [
  { icon: 'building', title: 'District officials', text: 'DDMA teams deciding where relief goes first.' },
  { icon: 'layers', title: 'State officials', text: 'SDMA teams comparing districts across the state.' },
  { icon: 'map', title: 'GIS analysts', text: 'Files that drop straight into existing workflows.' },
  { icon: 'search', title: 'Researchers', text: 'Reproducible results: scene IDs and Earth Engine code.' },
  { icon: 'sparkle', title: 'Students', text: 'Learn remote sensing on real Indian floods.' },
]

const SOURCES = ['Copernicus Sentinel-1', 'Copernicus Sentinel-2', 'Google Earth Engine', 'FAO GAUL boundaries',
  'GHSL population', 'WorldPop', 'JRC Global Surface Water', 'OpenStreetMap', 'Sen1Floods11']

function Stat({ value, decimals = 0, suffix = '', title, text, start }) {
  const shown = useCountTo(value, { decimals, start, ms: 1400 })
  return (
    <div className="stat-card reveal">
      <div className="stat-big">{shown}{suffix}</div>
      <div className="stat-title">{title}</div>
      <p>{text}</p>
    </div>
  )
}

export default function Landing({ section }) {
  const pageRef = useReveal([])
  const [statsRef, statsSeen] = useInView()
  const [liveRef, liveSeen] = useInView()

  // #/accuracy and friends scroll to their section, so the top-bar links
  // are real links that survive a refresh.
  useEffect(() => {
    if (!section) {
      window.scrollTo({ top: 0 })
      return
    }
    document.getElementById(section)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [section])

  return (
    <div className="landing" ref={pageRef}>
      <SiteHeader active={section} />

      <section className="hero">
        <div className="hero-glow" aria-hidden="true" />
        <div className="wrap hero-inner">
          <div className="hero-copy">
            <span className="hero-badge"><span className="dot" /> Sentinel-1 radar · sees through monsoon cloud</span>
            <h1>Satellite flood mapping for India, <span className="accent-text">with evidence you can check</span></h1>
            <p>
              Pick a district and dates. Antardrishti measures flood water from radar, counts the people
              living in it, and writes a report where every number is checked against the data.
            </p>
            <div className="hero-actions">
              <a className="btn btn-primary btn-lg" href={hrefFor('signup')}>
                Get started <Icon name="arrow" size={18} />
              </a>
              <a className="btn btn-ghost-light btn-lg" href={hrefFor('how')}>See how it works</a>
            </div>
            <ul className="hero-points">
              <li><Icon name="check" size={16} /> Free account, ready at once</li>
              <li><Icon name="check" size={16} /> English and हिन्दी</li>
              <li><Icon name="check" size={16} /> Measured accuracy on every result</li>
            </ul>
          </div>
          <HeroDemo />
        </div>
      </section>

      <div className="marquee" aria-label="Data sources">
        <div className="marquee-track">
          {[...SOURCES, ...SOURCES].map((s, i) => <span key={i}>{s}</span>)}
        </div>
      </div>

      <section className="section" id="services">
        <div className="wrap">
          <div className="section-head reveal">
            <span className="eyebrow">Services</span>
            <h2>What Antardrishti does</h2>
            <p>Built for disaster-management teams who need answers in hours, not days.</p>
          </div>
          <div className="grid services">
            {SERVICES.map((s, i) => (
              <article key={s.title} className="card service reveal" style={{ '--delay': `${i * 70}ms` }}>
                <div className="service-icon"><Icon name={s.icon} size={24} /></div>
                <h3>{s.title}</h3>
                <p>{s.text}</p>
              </article>
            ))}
          </div>

          <div className="beyond reveal">
            <div>
              <h3>More than floods</h3>
              <p>Seven analyses, each with its own measured accuracy shown before you run it.</p>
            </div>
            <ul className="analysis-chips">
              {ANALYSES.map((a) => {
                const view = reliabilityView(a)
                return (
                  <li key={a.name}>
                    <span>{a.name}</span>
                    <span className={`rel rel-${view.tone}`}>{view.label}</span>
                  </li>
                )
              })}
            </ul>
          </div>
        </div>
      </section>

      <section className="live-section" id="live" ref={liveRef}>
        <div className="wrap">
          {liveSeen || section === 'live' ? (
            <Suspense fallback={<div className="live-placeholder"><span className="spinner" /></div>}>
              <LandingLive />
            </Suspense>
          ) : <div className="live-placeholder" />}
        </div>
      </section>

      <section className="section alt" id="how">
        <div className="wrap">
          <div className="section-head reveal">
            <span className="eyebrow">How it works</span>
            <h2>From a question to a map in three steps</h2>
          </div>
          <ol className="steps">
            {STEPS.map((s, i) => (
              <li key={s.title} className="step reveal" style={{ '--delay': `${i * 120}ms` }}>
                <div className="step-n"><Icon name={s.icon} size={22} /><span>{i + 1}</span></div>
                <h3>{s.title}</h3>
                <p>{s.text}</p>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section className="section" id="accuracy">
        <div className="wrap">
          <div className="section-head reveal">
            <span className="eyebrow">Accuracy</span>
            <h2>Measured, and open about limits</h2>
            <p>
              Scored on Sen1Floods11, a public set of hand-labelled flood images from 11 countries,
              on images the method never saw while it was being tuned.
            </p>
          </div>
          <div className="grid stats" ref={statsRef}>
            <Stat value={0.61} decimals={2} start={statsSeen} title="Overlap with expert maps"
              text="IoU of our flood outline against the hand-drawn one, on held-out test images at 200 m." />
            <Stat value={77} suffix="%" start={statsSeen} title="Precision"
              text="When a pixel is marked as water it is water about three times in four (measured at 10 m)." />
            <Stat value={100} suffix="%" start={statsSeen} title="Report numbers traced"
              text="Every figure in a generated report is matched to the evidence record before it is shown." />
          </div>
          <div className="limits reveal">
            <Icon name="help" size={22} />
            <div>
              <h3>What it can't do yet</h3>
              <p>
                Water under dense trees and between tall buildings is often missed, so the true flooded
                area is usually larger than reported. Satellites pass every few days, so results are
                snapshots, not live video. Village and road names come from OpenStreetMap and may be incomplete.
              </p>
            </div>
          </div>
        </div>
      </section>

      <section className="section alt" id="who">
        <div className="wrap">
          <div className="section-head reveal">
            <span className="eyebrow">Who it's for</span>
            <h2>Built for the people who respond</h2>
          </div>
          <div className="grid who">
            {WHO.map((w, i) => (
              <div key={w.title} className="card who-card reveal" style={{ '--delay': `${i * 60}ms` }}>
                <Icon name={w.icon} size={22} />
                <h3>{w.title}</h3>
                <p>{w.text}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="cta-band">
        <div className="wrap cta-inner reveal">
          <div>
            <h2>Map your first flood in about a minute</h2>
            <p>Create a free account and you can run an analysis straight away.</p>
          </div>
          <div className="cta-actions">
            <a className="btn btn-primary btn-lg" href={hrefFor('signup')}>Create account</a>
            <a className="btn btn-ghost-light btn-lg" href={hrefFor('login')}>Log in</a>
          </div>
        </div>
      </section>

      <footer className="site-footer">
        <div className="wrap footer-inner">
          <div>
            <Logo />
            <p>Final-year B.Tech project, Parul University · Team NOGIXX.</p>
            <p className="footer-small">Earth observation for India, with every number traceable.</p>
          </div>
          <div>
            <h4>Data sources</h4>
            <p>Copernicus Sentinel-1 and 2 (ESA)<br />Google Earth Engine<br />FAO GAUL boundaries<br />GHSL · WorldPop · OpenStreetMap</p>
          </div>
          <div>
            <h4>Get started</h4>
            <p>
              <a href={hrefFor('signup')}>Create an account</a><br />
              <a href={hrefFor('login')}>Log in</a><br />
              <a href={hrefFor('accuracy')}>Accuracy and limits</a>
            </p>
          </div>
        </div>
        <div className="wrap footer-base">© {new Date().getFullYear()} Team NOGIXX · Contains modified Copernicus Sentinel data.</div>
      </footer>
    </div>
  )
}
