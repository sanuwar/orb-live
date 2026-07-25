const moments = [
  {
    time: 'Before 9:30',
    title: 'Premarket preparation',
    stage: 'Observe',
    tone: 'observe',
  },
  { time: '9:30', title: 'Opening bell', stage: 'Observe', tone: 'observe' },
  {
    time: '9:30–9:35',
    title: 'First five-minute range',
    stage: 'Observe',
    tone: 'observe',
  },
  {
    time: '9:40–11:00',
    title: 'Permitted entry window',
    stage: 'Narrow → Authorize',
    tone: 'narrow',
  },
  {
    time: 'After a fill',
    title: 'Protection & management',
    stage: 'Protect',
    tone: 'protect',
  },
  { time: '15:45', title: 'Time-based exit', stage: 'Prove', tone: 'prove' },
]

export default function StrategyTimeline() {
  return (
    <figure
      className="strategy-timeline diagram-card"
      aria-labelledby="trading-day-title"
    >
      <figcaption>
        <span className="section-label">The session clock</span>
        <h2 id="trading-day-title">When the shared stages occur</h2>
        <p>
          These are time anchors inside the five-stage story—not another
          decision process to memorize.
        </p>
      </figcaption>
      <ol>
        {moments.map((moment) => (
          <li key={moment.time} className={`timeline-${moment.tone}`}>
            <span className="timeline-dot" aria-hidden="true" />
            <span className="timeline-stage">{moment.stage}</span>
            <time>{moment.time}</time>
            <strong>{moment.title}</strong>
          </li>
        ))}
      </ol>
    </figure>
  )
}