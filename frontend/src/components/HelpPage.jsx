import { useReveal } from '../hooks'
import { hrefFor } from '../lib/router'
import HowItWorks from './HowItWorks'
import Icon from './Icon'

/** How it works, accuracy, limits and the glossary, as a page of its own. */
export default function HelpPage({ catalogue }) {
  const ref = useReveal([catalogue])
  return (
    <main className="page wrap help-page" ref={ref}>
      <div className="page-head">
        <div>
          <h1>Help</h1>
          <p>How Antardrishti works, how accurate each analysis is, what it cannot do, and what the words mean.</p>
        </div>
        <a className="btn btn-primary" href={hrefFor('new')}><Icon name="plus" size={18} /> New analysis</a>
      </div>
      <div className="help-jump reveal">
        <a href="#hiw-steps" onClick={(e) => { e.preventDefault(); document.getElementById('hiw-steps')?.scrollIntoView({ behavior: 'smooth' }) }}>
          <Icon name="satellite" size={20} /> From satellite to answer</a>
        <a href="#hiw-accuracy" onClick={(e) => { e.preventDefault(); document.getElementById('hiw-accuracy')?.scrollIntoView({ behavior: 'smooth' }) }}>
          <Icon name="shield" size={20} /> How accurate is it?</a>
        <a href="#hiw-limits" onClick={(e) => { e.preventDefault(); document.getElementById('hiw-limits')?.scrollIntoView({ behavior: 'smooth' }) }}>
          <Icon name="help" size={20} /> What it cannot do</a>
        <a href="#hiw-glossary" onClick={(e) => { e.preventDefault(); document.getElementById('hiw-glossary')?.scrollIntoView({ behavior: 'smooth' }) }}>
          <Icon name="language" size={20} /> Glossary</a>
      </div>
      <div className="card help-card reveal">
        <HowItWorks catalogue={catalogue} inline />
      </div>
    </main>
  )
}
