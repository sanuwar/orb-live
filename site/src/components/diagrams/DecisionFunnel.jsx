import { activeSignalConditions, strategyPresentation } from '../../data/strategyFacts'

const levels = [
  { label: 'Fixed watchlist', count: strategyPresentation.watchlist },
  { label: 'Opening move', count: strategyPresentation.gap },
  { label: 'Signal evidence', count: activeSignalConditions.join(' - ') },
  { label: 'Risk envelope', count: strategyPresentation.riskEnvelope },
  { label: 'Operator authorization', count: 'Explicit attended review required' },
]

export default function DecisionFunnel() {
  return (
    <figure className="decision-funnel diagram-card" aria-labelledby="decision-funnel-title">
      <figcaption>
        <span className="section-label">Active decision funnel</span>
        <h2 id="decision-funnel-title">10 symbols to 0-1 reviewed opportunity</h2>
        <p>Each displayed signal condition is a current canonical requirement. Any failed condition produces no candidate.</p>
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