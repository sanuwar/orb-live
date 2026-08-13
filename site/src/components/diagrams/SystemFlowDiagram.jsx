const equations = [
  {
    stage: 'Observe',
    slug: 'observe',
    formula: '10 → 5m + 20d',
    input: 'Fixed symbols',
    output: 'Range + volume baseline',
  },
  {
    stage: 'Narrow',
    slug: 'narrow',
    formula: '≥1% + ≥3× + 1m',
    input: 'Observed session',
    output: '0–1 candidate',
  },
  {
    stage: 'Authorize',
    slug: 'authorize',
    formula: '$15 ∩ $3k ∩ 1',
    input: 'Candidate + fresh state',
    output: '15s bounded intent',
  },
  {
    stage: 'Protect',
    slug: 'protect',
    formula: 'OR low → 1R → 2R',
    input: 'Actual filled quantity',
    output: '15:45 or earlier exit',
  },
  {
    stage: 'Prove',
    slug: 'prove',
    formula: 'Expected ≟ observed',
    input: 'Durable state + fresh snapshot',
    output: 'Evidence or operator stop',
  },
]

export default function SystemFlowDiagram() {
  return (
    <figure
      className="system-equation"
      aria-labelledby="system-flow-title"
      aria-describedby="system-flow-description"
    >
      <figcaption>
        <span className="section-label">Operational equation</span>
        <h2 id="system-flow-title">10 symbols → bounded evidence</h2>
        <p id="system-flow-description">
          Numbers drive each gate; durable state carries the result forward.
        </p>
      </figcaption>
      <ol>
        {equations.map((equation, index) => (
          <li key={equation.stage} className={`journey-${equation.slug}`}>
            <header>
              <span>{String(index + 1).padStart(2, '0')}</span>
              <strong>{equation.stage}</strong>
            </header>
            <div className="equation-formula">{equation.formula}</div>
            <dl>
              <div><dt>IN</dt><dd>{equation.input}</dd></div>
              <div><dt>OUT</dt><dd>{equation.output}</dd></div>
            </dl>
            {index < equations.length - 1 && <span className="equation-arrow" aria-hidden="true">→</span>}
          </li>
        ))}
      </ol>
    </figure>
  )
}