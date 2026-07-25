import { flowStages } from '../../data/content'

const legend = [
  { kind: 'observe', label: 'Market observation' },
  { kind: 'hypothetical', label: 'Hypothetical / shadow decision' },
  { kind: 'reviewed', label: 'Reviewed or verified' },
  { kind: 'authorized', label: 'Authorized broker write boundary' },
]

export default function SystemFlowDiagram() {
  return (
    <figure
      className="system-flow diagram-card"
      aria-labelledby="system-flow-title"
      aria-describedby="system-flow-description"
    >
      <figcaption>
        <span className="section-label">Conceptual pipeline</span>
        <h2 id="system-flow-title">From observation to resolution</h2>
        <p id="system-flow-description">
          These are safety-separated stages, not a claim that the current
          system automatically advances through them.
        </p>
      </figcaption>
      <div className="diagram-legend" aria-label="Diagram legend">
        {legend.map((item) => (
          <span key={item.kind} className={`legend-${item.kind}`}>
            <i aria-hidden="true" />
            {item.label}
          </span>
        ))}
      </div>
      <ol className="flow-grid">
        {flowStages.map((stage, index) => (
          <li key={stage.title} className={`flow-stage stage-${stage.kind}`}>
            <article tabIndex="0">
              <span className="flow-meta">
                {stage.group} · {String(index + 1).padStart(2, '0')}
              </span>
              <strong>{stage.title}</strong>
              <p>{stage.description}</p>
            </article>
            {index < flowStages.length - 1 && (
              <svg
                className="flow-arrow"
                viewBox="0 0 32 16"
                aria-hidden="true"
              >
                <path d="M1 8h26M21 2l6 6-6 6" />
              </svg>
            )}
          </li>
        ))}
      </ol>
    </figure>
  )
}
