import { journeyStages } from '../data/content'

export default function JourneySpine({
  focus = [],
  title = 'One system story',
  description = 'The same five stages organize every view of ORB-LIVE.',
  compact = false,
}) {
  return (
    <figure
      className={`journey-spine${compact ? ' journey-spine-compact' : ''}`}
      aria-labelledby="journey-spine-title"
    >
      <figcaption>
        <span className="section-label">Shared narrative</span>
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
              <span className="journey-number" aria-hidden="true">
                {stage.number}
              </span>
              <div>
                <strong>{stage.title}</strong>
                {!compact && <p>{stage.summary}</p>}
                <small>{stage.question}</small>
              </div>
            </li>
          )
        })}
      </ol>
    </figure>
  )
}