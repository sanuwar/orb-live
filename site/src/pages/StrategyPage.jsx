import Callout from '../components/Callout'
import PageHeader from '../components/PageHeader'
import MeasurementPipeline from '../components/MeasurementPipeline'
import StrategyTimeline from '../components/diagrams/StrategyTimeline'
import { activeSignalConditions, strategyPresentation } from '../data/strategyFacts'

const examples = [
  { title: 'Illustrative Case A', detail: 'Strong gap and RVOL. Price crosses the opening-range high, leaves a large upper wick, and closes only slightly above the range.', outcome: 'Weak hold or confirmation.' },
  { title: 'Illustrative Case B', detail: 'Moderate gap and strong RVOL. Price decisively clears the opening-range high, leaves a small upper wick, and closes near the bar high.', outcome: 'Stronger breakout behavior.' },
]

export default function StrategyPage() {
  return <div className="page">
    <PageHeader eyebrow="How ORB-LIVE Decides" title="10 symbols. 8 measurements. 80 minutes." lead="This explains the existing orb_long_v0 signal conditions. It does not add a score, ranking rule, or trading control." />
    <MeasurementPipeline />
    <StrategyTimeline />
    <section aria-labelledby="active-logic"><span className="section-label">Current active logic</span><h2 id="active-logic">Every required signal condition</h2><ul>{activeSignalConditions.map((condition) => <li key={condition}>{condition}</li>)}</ul><p>{strategyPresentation.openingRange} establishes the breakout reference. SPY/VWAP is shown because the current canonical configuration enables that filter.</p></section>
    <section aria-labelledby="breakout-examples"><span className="section-label">Illustrative example - not live trade data</span><h2 id="breakout-examples">High gap plus high RVOL does not automatically mean a strong breakout</h2><div className="area-card-grid">{examples.map((example) => <article key={example.title}><h3>{example.title}</h3><div aria-label="Illustrative opening-range breakout diagram"><p>OR high: ----------------</p><p>Price bar: | candle |</p></div><p>{example.detail}</p><strong>{example.outcome}</strong></article>)}</div></section>
    <section className="operations-unavailable"><span className="section-label">Real ORB-LIVE session</span><h2>REAL CASE STUDY PENDING SUFFICIENT EVIDENCE</h2><p>Only authenticated, sanitized session evidence may populate a real case study. No historic result is inferred.</p></section>
    <Callout title="Research is not active strategy logic" tone="neutral" icon="i"><p>Breakout displacement, close location, wick size, and future deterministic candidate ranking are research concepts only. They are not production rules in orb_long_v0.</p></Callout>
  </div>
}