import { milestones } from '../../data/content'

function statusClass(status) {
  return status.toLowerCase().replaceAll(' ', '-')
}

export default function DevelopmentTimeline() {
  return (
    <figure
      className="development-timeline diagram-card"
      aria-labelledby="development-timeline-title"
    >
      <figcaption>
        <span className="section-label">Milestone history</span>
        <h2 id="development-timeline-title">Built in deliberate safety layers</h2>
        <p>
          Status describes engineering readiness—not authorization to trade.
        </p>
      </figcaption>
      <ol>
        {milestones.map((milestone, index) => (
          <li key={milestone.title}>
            <span className="milestone-number" aria-hidden="true">
              {String(index + 1).padStart(2, '0')}
            </span>
            <article>
              <span
                className={`status-pill status-${statusClass(milestone.status)}`}
              >
                {milestone.status}
              </span>
              <h3>{milestone.title}</h3>
              <p>{milestone.detail}</p>
            </article>
          </li>
        ))}
      </ol>
    </figure>
  )
}
