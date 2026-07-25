import { flowStages, journeyStages } from '../../data/content'

export default function SystemFlowDiagram() {
  return (
    <figure
      className="system-flow diagram-card"
      aria-labelledby="system-flow-title"
      aria-describedby="system-flow-description"
    >
      <figcaption>
        <span className="section-label">One pipeline · five chapters</span>
        <h2 id="system-flow-title">The shared thread, with technical detail</h2>
        <p id="system-flow-description">
          Each chapter contains two responsibilities. They are boundaries, not
          a claim that the current system automatically advances through them.
        </p>
      </figcaption>
      <ol className="flow-chapters">
        {journeyStages.map((journeyStage) => {
          const details = flowStages.filter(
            (stage) => stage.group === journeyStage.slug,
          )
          return (
            <li
              key={journeyStage.slug}
              className={`flow-chapter journey-${journeyStage.slug}`}
            >
              <header>
                <span aria-hidden="true">{journeyStage.number}</span>
                <strong>{journeyStage.title}</strong>
                <small>{journeyStage.question}</small>
              </header>
              <ol>
                {details.map((detail) => (
                  <li key={detail.title} tabIndex="0">
                    <strong>{detail.title}</strong>
                    <p>{detail.description}</p>
                  </li>
                ))}
              </ol>
            </li>
          )
        })}
      </ol>
    </figure>
  )
}