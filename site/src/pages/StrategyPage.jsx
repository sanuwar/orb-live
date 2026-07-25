import Callout from '../components/Callout'
import JourneySpine from '../components/JourneySpine'
import MetricCard from '../components/MetricCard'
import PageHeader from '../components/PageHeader'
import StrategyTimeline from '../components/diagrams/StrategyTimeline'
import { riskConstraints, strategySteps } from '../data/content'

const strategyGroups = [
  {
    slug: 'observe',
    title: 'Observe the session',
    question: 'What is happening?',
    summary: 'Establish a bounded universe and the opening-range reference.',
    steps: [strategySteps[0], strategySteps[2]],
  },
  {
    slug: 'narrow',
    title: 'Narrow to a qualified setup',
    question: 'Does it fit the strategy?',
    summary: 'Require price, participation, market context, and confirmation.',
    steps: [strategySteps[1], strategySteps[3], strategySteps[4], strategySteps[5]],
  },
]

export default function StrategyPage() {
  return (
    <div className="page">
      <PageHeader
        eyebrow="ORB V0 · long only"
        title="Wait for the range. Confirm the move. Bound the risk."
        lead="The strategy watches a fixed set of symbols, waits for the opening noise to settle into a five-minute range, and evaluates only confirmed breakouts that satisfy every filter."
      />

      <JourneySpine
        focus={['observe', 'narrow']}
        compact
        title="Strategy begins the shared story"
        description="This page concentrates on Observe and Narrow. Authorization, protection, and proof belong to later boundaries."
      />

      <StrategyTimeline />

      <section aria-labelledby="strategy-steps-title">
        <div className="section-heading">
          <span className="section-label">Two strategy chapters</span>
          <h2 id="strategy-steps-title">Six details, organized by purpose</h2>
          <p>
            The filters are supporting checks inside Observe and Narrow—not six
            more top-level stages.
          </p>
        </div>
        <div className="strategy-filter-groups">
          {strategyGroups.map((group) => (
            <article
              key={group.slug}
              className={`strategy-filter-group journey-${group.slug}`}
            >
              <header>
                <span className="stage-chip">{group.title}</span>
                <h3>{group.question}</h3>
                <p>{group.summary}</p>
              </header>
              <div className="disclosure-grid">
                {group.steps.map((step) => (
                  <details key={step.title}>
                    <summary>
                      <div>
                        <strong>{step.title}</strong>
                        <p>{step.summary}</p>
                      </div>
                    </summary>
                    <p className="details-body">{step.detail}</p>
                  </details>
                ))}
              </div>
            </article>
          ))}
        </div>
      </section>

      <section className="management-grid" aria-labelledby="management-title">
        <div>
          <span className="section-label">Protect · then prove</span>
          <h2 id="management-title">A predefined management path</h2>
          <p>
            The design begins with the opening-range low as the initial stop,
            moves the stop to the entry price at 1R, targets a full exit at
            2R, and does not carry the position overnight.
          </p>
          <ol className="management-steps">
            <li><span>1</span><div><strong>Initial protection</strong><p>Stop reference: the opening-range low.</p></div></li>
            <li><span>2</span><div><strong>Breakeven transition</strong><p>At 1R, the designed stop reference moves to entry.</p></div></li>
            <li><span>3</span><div><strong>Profit or time exit</strong><p>Full target at 2R; otherwise exit by 15:45.</p></div></li>
          </ol>
        </div>
        <details className="r-explainer">
          <summary>
            <span className="r-symbol" aria-hidden="true">R</span>
            <div><strong>Understanding R</strong><p>A common way to express reward relative to planned risk.</p></div>
          </summary>
          <div className="r-example">
            <span className="fictional-pill">Fictional example</span>
            <p>
              Imagine an entry at $50 and an initial stop at $49.50. The
              50-cent distance is 1R per share. A 2R price objective would be
              $51—twice that distance above entry.
            </p>
            <p>R standardizes the plan; it does not predict the fill price or guarantee the realized loss.</p>
          </div>
        </details>
      </section>

      <section aria-labelledby="risk-title">
        <div className="section-heading">
          <span className="section-label">Authorize · design constraints</span>
          <h2 id="risk-title">Risk limits are inputs, not promises</h2>
        </div>
        <div className="metrics-grid risk-metrics">
          {riskConstraints.map((constraint) => (
            <MetricCard key={constraint.label} value={constraint.value} label={constraint.label} />
          ))}
        </div>
        <Callout title="Real outcomes can differ" tone="neutral" icon="i">
          <p>
            Gaps, slippage, partial fills, latency, and broker behavior can
            produce losses beyond a planned amount. These values constrain
            design decisions; they do not eliminate market risk.
          </p>
        </Callout>
      </section>
    </div>
  )
}