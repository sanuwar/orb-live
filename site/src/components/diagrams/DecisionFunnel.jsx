const levels = [
  { label: 'Fixed watchlist', count: '15 symbols' },
  { label: 'Gap and liquidity context', count: 'Fewer candidates' },
  { label: 'Range, volume, and SPY filters', count: 'Qualified setups' },
  { label: 'Risk and safety gates', count: 'Zero or one opportunity' },
  { label: 'Explicit operator authorization', count: 'Bounded intent' },
]

export default function DecisionFunnel() {
  return (
    <figure
      className="decision-funnel diagram-card"
      aria-labelledby="decision-funnel-title"
    >
      <figcaption>
        <span className="section-label">Selection model</span>
        <h2 id="decision-funnel-title">Decision Funnel</h2>
        <p>
          Every layer can reject a candidate. Reaching the end does not
          authorize live trading by itself.
        </p>
      </figcaption>
      <ol>
        {levels.map((level, index) => (
          <li
            key={level.label}
            style={{ '--funnel-width': `${100 - index * 13}%` }}
          >
            <span>{level.label}</span>
            <strong>{level.count}</strong>
          </li>
        ))}
      </ol>
    </figure>
  )
}
