import { termFor, tooltip } from '../lib/glossary'

/**
 * A technical word that explains itself: dotted underline, definition on
 * hover or focus. Unknown keys render the text plainly - a missing entry is
 * caught by a test, not by a broken page.
 */
export default function Term({ k, children }) {
  const entry = termFor(k)
  if (!entry) return <>{children}</>
  return (
    <abbr className="term" title={tooltip(k)} tabIndex={0} data-term={k}>
      {children ?? entry.term}
    </abbr>
  )
}
