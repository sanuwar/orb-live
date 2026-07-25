import Callout from '../components/Callout'
import MetricCard from '../components/MetricCard'
import PageHeader from '../components/PageHeader'
import StrategyTimeline from '../components/diagrams/StrategyTimeline'
import { riskConstraints, strategySteps } from '../data/content'

export default function StrategyPage() {
  return (
    <div className="page">
      <PageHeader
        eyebrow="ORB V0 · long only"
        title="Wait for the range. Confirm the move. Bound the risk."
        lead="The strategy watches a fixed set of symbols, waits for the opening noise to settle into a five-minute range, and evaluates only confirmed breakouts that satisfy every filter."
      />

      <StrategyTimeline />

      <section aria-labelledby="strategy-steps-title">
        <div className="section-heading">
          <span className="section-label">Progressive filters</span>
          <h2 id="strategy-steps-title">How a candidate earns attention</h2>
          <p>
            Open any step for the technical detail. A candidate that fails one
            layer goes no further.
          </p>
        </div>
        <div className="disclosure-grid">
          {strategySteps.map((step, index) => (
            <details key={step.title}>
              <summary>
                <span>{String(index + 1).padStart(2, '0')}</span>
                <div>
                  <strong>{step.title}</strong>
                  <p>{step.summary}</p>
                </div>
              </summary>
              <p className="details-body">{step.detail}</p>
            </details>
          ))}
        </div>
      </section>

      <section className="management-grid" aria-labelledby="management-title">
        <div>
          <span className="section-label">After entry</span>
          <h2 id="management-title">A predefined management path</h2>
          <p>
            The design begins with the opening-range low as the initial stop,
            moves the stop to the entry price at 1R, targets a full exit at
            2R, and does not carry the position overnight.
          </p>
          <ol className="management-steps">
            <li>
              <span>1</span>
              <div>
                <strong>Initial protection</strong>
                <p>Stop reference: the opening-range low.</p>
              </div>
            </li>
            <li>
              <span>2</span>
              <div>
                <strong>Breakeven transition</strong>
                <p>At 1R, the designed stop reference moves to entry.</p>
              </div>
            </li>
            <li>
              <span>3</span>
              <div>
                <strong>Profit or time exit</strong>
                <p>Full target at 2R; otherwise exit by 15:45.</p>
              </div>
            </li>
          </ol>
        </div>
        <details className="r-explainer">
          <summary>
            <span className="r-symbol" aria-hidden="true">
              R
            </span>
            <div>
              <strong>Understanding R</strong>
              <p>A common way to express reward relative to planned risk.</p>
            </div>
          </summary>
          <div className="r-example">
            <span className="fictional-pill">Fictional example</span>
            <p>
              Imagine an entry at $50 and an initial stop at $49.50. The
              50-cent distance is 1R per share. A 2R price objective would be
              $51—twice that distance above entry.
            </p>
            <p>
              R standardizes the plan; it does not predict the fill price or
              guarantee the realized loss.
            </p>
          </div>
        </details>
      </section>

      <section aria-labelledby="risk-title">
        <div className="section-heading">
          <span className="section-label">Design constraints</span>
          <h2 id="risk-title">Risk limits are inputs, not promises</h2>
        </div>
        <div className="metrics-grid risk-metrics">
          {riskConstraints.map((constraint) => (
            <MetricCard
              key={constraint.label}
              value={constraint.value}
              label={constraint.label}
            />
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
