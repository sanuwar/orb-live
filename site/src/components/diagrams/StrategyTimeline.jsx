const moments = [
  { time: 'Before 9:30', title: 'Premarket preparation', tone: 'quiet' },
  { time: '9:30', title: 'Opening bell', tone: 'observe' },
  { time: '9:30–9:35', title: 'First five-minute range', tone: 'observe' },
  { time: '9:40–11:00', title: 'Permitted entry window', tone: 'decide' },
  { time: 'After a fill', title: 'Protection & management', tone: 'protect' },
  { time: '15:45', title: 'Time-based exit', tone: 'close' },
]

export default function StrategyTimeline() {
  return (
    <figure
      className="strategy-timeline diagram-card"
      aria-labelledby="trading-day-title"
    >
      <figcaption>
        <span className="section-label">Session map</span>
        <h2 id="trading-day-title">Trading Day Timeline</h2>
        <p>
          The design deliberately waits, filters, and limits when a setup
          can be considered.
        </p>
      </figcaption>
      <ol>
        {moments.map((moment, index) => (
          <li key={moment.time} className={`timeline-${moment.tone}`}>
            <span className="timeline-index" aria-hidden="true">
              {String(index + 1).padStart(2, '0')}
            </span>
            <time>{moment.time}</time>
            <strong>{moment.title}</strong>
          </li>
        ))}
      </ol>
    </figure>
  )
}
