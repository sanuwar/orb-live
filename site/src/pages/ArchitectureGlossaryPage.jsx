import { useMemo, useState } from 'react'
import Callout from '../components/Callout'
import PageHeader from '../components/PageHeader'
import ArchitectureMap from '../components/diagrams/ArchitectureMap'
import { glossaryTerms } from '../data/glossary'

export default function ArchitectureGlossaryPage() {
  const [query, setQuery] = useState('')
  const filteredTerms = useMemo(() => {
    const normalized = query.trim().toLowerCase()
    if (!normalized) return glossaryTerms
    return glossaryTerms.filter(
      ({ term, definition }) =>
        term.toLowerCase().includes(normalized) ||
        definition.toLowerCase().includes(normalized),
    )
  }, [query])

  return (
    <div className="page">
      <PageHeader
        eyebrow="Architecture & glossary"
        title="Narrow layers, explicit boundaries, shared language."
        lead="The architecture separates strategy logic from authorization, durable workflows from broker capabilities, and automated evidence from operator judgment."
      />

      <ArchitectureMap />

      <Callout title="Frontend isolation" tone="safe" icon="✓">
        <p>
          This React application ships sanitized static content only. It does
          not import the Python application, connect to a backend, read
          repository state, or make runtime network requests.
        </p>
      </Callout>

      <section className="glossary-section" aria-labelledby="glossary-title">
        <div className="section-heading section-heading-row">
          <div>
            <span className="section-label">Shared vocabulary</span>
            <h2 id="glossary-title">Glossary</h2>
          </div>
          <label className="search-field">
            <span>Filter terms</span>
            <span className="search-control">
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <circle cx="11" cy="11" r="6" />
                <path d="m16 16 4 4" />
              </svg>
              <input
                type="search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search glossary"
              />
            </span>
          </label>
        </div>
        <p className="result-count" aria-live="polite">
          {filteredTerms.length}{' '}
          {filteredTerms.length === 1 ? 'term' : 'terms'} shown
        </p>
        {filteredTerms.length > 0 ? (
          <dl className="glossary-grid">
            {filteredTerms.map(({ term, definition }) => (
              <div key={term}>
                <dt>{term}</dt>
                <dd>{definition}</dd>
              </div>
            ))}
          </dl>
        ) : (
          <div className="no-results">
            <strong>No matching term</strong>
            <p>Try a broader word such as risk, order, or evidence.</p>
          </div>
        )}
      </section>
    </div>
  )
}
