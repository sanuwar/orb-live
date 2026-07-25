import { journeyStages, safetyLayers } from '../../data/content'

export default function SafetyLayers() {
  return (
    <figure
      className="safety-layers diagram-card"
      aria-labelledby="safety-layers-title"
    >
      <figcaption>
        <span className="section-label">Defense along the shared thread</span>
        <h2 id="safety-layers-title">Two safeguards reinforce each stage</h2>
        <p>
          The controls are easier to understand as protection attached to the
          system journey—not as a separate ten-step process.
        </p>
      </figcaption>
      <ol>
        {journeyStages.map((stage, index) => (
          <li
            key={stage.slug}
            className={`safety-stage journey-${stage.slug}`}
            tabIndex="0"
          >
            <header>
              <span aria-hidden="true">{stage.number}</span>
              <strong>{stage.title}</strong>
            </header>
            <ul>
              {safetyLayers.slice(index * 2, index * 2 + 2).map((layer) => (
                <li key={layer.title}>
                  <strong>{layer.title}</strong>
                  <p>{layer.detail}</p>
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ol>
    </figure>
  )
}