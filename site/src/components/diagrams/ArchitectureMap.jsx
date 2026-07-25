import { architectureLayers } from '../../data/content'

export default function ArchitectureMap() {
  return (
    <figure
      className="architecture-map diagram-card"
      aria-labelledby="architecture-map-title"
    >
      <figcaption>
        <span className="section-label">Conceptual architecture</span>
        <h2 id="architecture-map-title">Boundaries are part of the design</h2>
        <p>
          Each layer has a narrow job. The dashboard sits outside the live
          broker path and reads no runtime state.
        </p>
      </figcaption>
      <ol>
        {architectureLayers.map((layer, index) => (
          <li
            key={layer.title}
            className={
              index === architectureLayers.length - 1
                ? 'architecture-isolated'
                : undefined
            }
          >
            <span>{String(index + 1).padStart(2, '0')}</span>
            <div>
              <strong>{layer.title}</strong>
              <p>{layer.detail}</p>
            </div>
          </li>
        ))}
      </ol>
      <div className="isolation-line" aria-label="Isolation boundary">
        Static documentation isolation boundary
      </div>
    </figure>
  )
}
