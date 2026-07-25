import Callout from '../components/Callout'
import MetricCard from '../components/MetricCard'
import PageHeader from '../components/PageHeader'

const objectives = [
  {
    number: '01',
    title: 'Detect with discipline',
    text: 'Evaluate a fixed universe for opening-range opportunities only after predetermined evidence aligns.',
  },
  {
    number: '02',
    title: 'Constrain the downside',
    text: 'Size hypothetical trades from an initial stop and reject plans outside configured risk boundaries.',
  },
  {
    number: '03',
    title: 'Preserve control',
    text: 'Keep authorization explicit, state durable, outcomes reconciled, and uncertainty visible to the operator.',
  },
]

export default function OverviewPage() {
  return (
    <div className="page">
      <PageHeader
        eyebrow="Internal system explainer"
        title="A disciplined opening-range system, designed around proof."
        lead="ORB-LIVE explores how a long-only opening-range breakout strategy can be engineered with explicit risk boundaries, durable evidence, and human control."
      >
        <div className="hero-actions">
          <a className="primary-button" href="#/strategy">
            Begin the strategy walkthrough
            <span aria-hidden="true">→</span>
          </a>
          <span className="creator-line">
            Created by <strong>Sanuwar Rashid</strong>
          </span>
        </div>
      </PageHeader>

      <Callout title="Controlled commissioning" tone="warning" icon="!">
        <p>
          Strategy execution remains disabled. Checkpoint 7C is blocked, and
          active protective-stop verification remains unresolved. This site
          explains the work; it cannot operate it.
        </p>
      </Callout>

      <section aria-labelledby="objective-title">
        <div className="section-heading">
          <span className="section-label">Purpose</span>
          <h2 id="objective-title">Three objectives shape the system</h2>
          <p>
            The goal is not to predict every move. It is to make a narrow
            decision process observable, bounded, and reviewable.
          </p>
        </div>
        <div className="objective-grid">
          {objectives.map((objective) => (
            <article className="objective-card" key={objective.title}>
              <span>{objective.number}</span>
              <h3>{objective.title}</h3>
              <p>{objective.text}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="comparison-section" aria-labelledby="scope-title">
        <div className="section-heading">
          <span className="section-label">Scope</span>
          <h2 id="scope-title">Clear about what it is—and what it is not</h2>
        </div>
        <div className="comparison-grid">
          <article className="comparison-card comparison-does">
            <span className="comparison-icon" aria-hidden="true">
              ✓
            </span>
            <h3>What it does</h3>
            <ul>
              <li>Models a fixed, long-only ORB V0 strategy</li>
              <li>Separates observation, decision, and authorization</li>
              <li>Persists lifecycle state and uncertain outcomes</li>
              <li>Tests safety behavior with fixtures and simulation</li>
            </ul>
          </article>
          <article className="comparison-card comparison-does-not">
            <span className="comparison-icon" aria-hidden="true">
              ×
            </span>
            <h3>What it does not do</h3>
            <ul>
              <li>Promise profitability or guarantee limited losses</li>
              <li>Authorize unattended or automatic live trading</li>
              <li>Connect this dashboard to Robinhood</li>
              <li>Expose order controls, live data, or account state</li>
            </ul>
          </article>
        </div>
      </section>

      <section aria-labelledby="numbers-title">
        <div className="section-heading section-heading-row">
          <div>
            <span className="section-label">At a glance</span>
            <h2 id="numbers-title">A small strategy, extensively exercised</h2>
          </div>
          <p>
            Metrics describe repository design and test coverage, not trading
            performance.
          </p>
        </div>
        <div className="metrics-grid">
          <MetricCard
            value="1,609"
            label="Repository tests"
            note="At the time this dashboard was created"
          />
          <MetricCard value="6" label="Strategy filters" note="Before risk gates" />
          <MetricCard value="10" label="Defense layers" note="Conceptual controls" />
          <MetricCard value="0" label="Dashboard broker calls" note="Static by design" />
        </div>
      </section>
    </div>
  )
}
