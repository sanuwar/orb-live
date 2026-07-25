import { safetyLayers } from '../../data/content'

export default function SafetyLayers() {
  return (
    <figure
      className="safety-layers diagram-card"
      aria-labelledby="safety-layers-title"
    >
      <figcaption>
        <span className="section-label">Defense in depth</span>
        <h2 id="safety-layers-title">Ten controls, one fail-closed posture</h2>
        <p>
          No single check carries the entire safety case. Each layer narrows
          permission or strengthens evidence.
        </p>
      </figcaption>
      <ol>
        {safetyLayers.map((layer, index) => (
          <li key={layer.title} tabIndex="0">
            <span>{String(index + 1).padStart(2, '0')}</span>
            <div>
              <strong>{layer.title}</strong>
              <p>{layer.detail}</p>
            </div>
          </li>
        ))}
      </ol>
    </figure>
  )
}
