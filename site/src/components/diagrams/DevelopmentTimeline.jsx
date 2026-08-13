import { buildJourneyPhases } from '../../data/content'

export default function DevelopmentTimeline() {
  return (
    <section className="build-journey-timeline" aria-labelledby="build-journey-timeline-title">
      <div className="section-heading">
        <span className="section-label">Product flow</span>
        <h2 id="build-journey-timeline-title">Built through bounded, reviewable milestones</h2>
        <p>Completed engineering work is distinct from the attended commissioning frontier and future product work.</p>
      </div>
      <ol className="build-phase-list">
        {buildJourneyPhases.map((phase) => (
          <li className={`build-phase build-phase-${phase.status.toLowerCase()}`} key={phase.title}>
            <header>
              <span className="phase-marker" aria-hidden="true">{phase.status === 'CURRENT' ? '●' : '✓'}</span>
              <div><span className="section-label">{phase.status}</span><h3>{phase.title}</h3></div>
            </header>
            <ol className="build-milestone-grid">
              {phase.milestones.map((milestone) => (
                <li key={milestone.id}>
                  <article>
                    <span className="milestone-id">{milestone.id}</span>
                    <h4>{milestone.title}</h4>
                    <p>{milestone.description}</p>
                    <small>{milestone.validation}</small>
                  </article>
                </li>
              ))}
            </ol>
          </li>
        ))}
      </ol>
    </section>
  )
}
