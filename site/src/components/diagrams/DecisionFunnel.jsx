const levels = [
  { label: 'Fixed watchlist', count: '10 symbols' },
  { label: 'Opening move', count: '≥1.0% gap' },
  { label: 'Signal evidence', count: '5m · ≥3× · SPY>VWAP · 1m' },
  { label: 'Risk envelope', count: '$15 · $3k · 1 position' },
  { label: 'Operator authorization', count: '15s → 0–1 opportunity' },
]

export default function DecisionFunnel() {
  return (
    <figure className="decision-funnel diagram-card" aria-labelledby="decision-funnel-title">
      <figcaption>
        <span className="section-label">Numeric decision funnel</span>
        <h2 id="decision-funnel-title">10 symbols → 0–1 authorized opportunity</h2>
        <p>Any failed threshold produces zero.</p>
      </figcaption>
      <ol>
        {levels.map((level, index) => (
          <li key={level.label} style={{ '--funnel-width': `${100 - index * 13}%` }}>
            <span>{level.label}</span>
            <strong>{level.count}</strong>
          </li>
        ))}
      </ol>
    </figure>
  )
}