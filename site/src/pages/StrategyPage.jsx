import Callout from '../components/Callout'
import JourneySpine from '../components/JourneySpine'
import MeasurementPipeline from '../components/MeasurementPipeline'
import PageHeader from '../components/PageHeader'
import StrategyTimeline from '../components/diagrams/StrategyTimeline'

export default function StrategyPage() {
  return (
    <div className="page">
      <PageHeader
        eyebrow="ORB V0 · long only"
        title="15 symbols. 8 measurements. 80 minutes."
        lead="A candidate survives only when every configured observation and qualification measurement passes."
      />

      <JourneySpine
        focus={['observe', 'narrow']}
        compact
        title="Observe + Narrow"
        description="6 primary stage metrics summarize the measurement path; the instrument board below shows how each value is captured and used."
      />

      <MeasurementPipeline />
      <StrategyTimeline />

      <section className="management-grid" aria-labelledby="management-title">
        <div>
          <span className="section-label">Protection sequence</span>
          <h2 id="management-title">OR low → 1R → 2R → 15:45</h2>
          <ol className="management-steps">
            <li><span>0R</span><div><strong>Initial stop</strong><p>Opening-range low.</p></div></li>
            <li><span>1R</span><div><strong>Breakeven</strong><p>Designed stop reference moves to entry.</p></div></li>
            <li><span>2R</span><div><strong>Target</strong><p>Designed full profit exit.</p></div></li>
            <li><span>15:45</span><div><strong>Time exit</strong><p>Zero overnight positions.</p></div></li>
          </ol>
        </div>
        <details className="r-explainer">
          <summary>
            <span className="r-symbol" aria-hidden="true">R</span>
            <div><strong>1R = entry − initial stop</strong><p>Open the fictional numeric example.</p></div>
          </summary>
          <div className="r-example">
            <span className="fictional-pill">Fictional example</span>
            <p>$50.00 entry − $49.50 stop = $0.50 per share = 1R.</p>
            <p>$51.00 target = $1.00 above entry = 2R.</p>
          </div>
        </details>
      </section>

      <Callout title="Design limits are not outcome guarantees" tone="neutral" icon="i">
        <p>Gaps, slippage, partial fills, latency, and broker behavior can exceed planned risk.</p>
      </Callout>
    </div>
  )
}