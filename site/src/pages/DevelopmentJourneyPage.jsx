import PageHeader from '../components/PageHeader'
import DevelopmentTimeline from '../components/diagrams/DevelopmentTimeline'

const developmentLoop = [
  {
    title: 'Proposal',
    detail: 'Define a narrow milestone, constraints, and stop conditions.',
  },
  {
    title: 'Offline implementation',
    detail: 'Build without live broker calls or production data.',
  },
  {
    title: 'Fixture testing',
    detail: 'Exercise success, failure, ambiguity, crash, and replay cases.',
  },
  {
    title: 'Pull request & CI',
    detail: 'Review a focused diff and rerun the complete automated suite.',
  },
  {
    title: 'Attended checkpoint',
    detail: 'Only separately authorized, bounded validation can touch live context.',
  },
  {
    title: 'Evidence',
    detail: 'Record sanitized proof of what was and was not established.',
  },
  {
    title: 'Handoff',
    detail: 'State the exact baseline, remaining risks, and unresolved facts.',
  },
  {
    title: 'Next safety gate',
    detail: 'Advance only after prerequisites are directly grounded.',
  },
]

export default function DevelopmentJourneyPage() {
  return (
    <div className="page">
      <PageHeader
        eyebrow="Development journey"
        title="Maturity comes from closing one uncertainty at a time."
        lead="ORB-LIVE has progressed from strategy rules to durable state, deterministic simulation, reconciliation, and carefully bounded commissioning work. Unfinished validation stays visibly unfinished."
      />

      <DevelopmentTimeline />

      <section aria-labelledby="method-title">
        <div className="section-heading">
          <span className="section-label">Working method</span>
          <h2 id="method-title">How the project is developed</h2>
          <p>
            Each milestone is deliberately small enough to review, test, and
            stop without granting authority to the next one.
          </p>
        </div>
        <ol className="development-loop">
          {developmentLoop.map((step, index) => (
            <li key={step.title}>
              <span>{String(index + 1).padStart(2, '0')}</span>
              <div>
                <strong>{step.title}</strong>
                <p>{step.detail}</p>
              </div>
            </li>
          ))}
        </ol>
      </section>
    </div>
  )
}
