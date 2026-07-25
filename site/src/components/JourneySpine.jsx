import { journeyStages } from '../data/content'

export default function JourneySpine({
  focus = [],
  title = 'Five stages. Fifteen operating numbers.',
  description = 'Configuration, thresholds, limits, and current proof status.',
  compact = false,
}) {
  return (
    <figure
      className={`journey-spine${compact ? ' journey-spine-compact' : ''}`}
      aria-labelledby="journey-spine-title"
    >
      <figcaption>
        <span className="section-label">Quantified system</span>
        <h2 id="journey-spine-title">{title}</h2>
        <p>{description}</p>
      </figcaption>
      <ol>
        {journeyStages.map((stage) => {
          const isFocused = focus.length === 0 || focus.includes(stage.slug)
          return (
            <li
              key={stage.slug}
              className={`journey-stage journey-${stage.slug}${
                isFocused ? ' journey-stage-focused' : ''
              }`}
            >
              <header>
                <span className="journey-number" aria-hidden="true">{stage.number}</span>
                <strong>{stage.title}</strong>
                <small>{stage.question}</small>
              </header>
              <dl>
                {stage.metrics.map((metric) => (
                  <div key={metric.label}>
                    <dt>{metric.value}</dt>
                    <dd>{metric.label}</dd>
                  </div>
                ))}
              </dl>
            </li>
          )
        })}
      </ol>
    </figure>
  )
}