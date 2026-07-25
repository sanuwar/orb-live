import Callout from '../components/Callout'
import PageHeader from '../components/PageHeader'
import ProtectionStateMap from '../components/diagrams/ProtectionStateMap'
import SafetyLayers from '../components/diagrams/SafetyLayers'

const boundaries = [
  'No margin',
  'No short selling',
  'No averaging down',
  'One open position',
  'Limited daily entries',
  'Daily and weekly loss stops',
  'Total drawdown halt',
  'No automatic retry of an uncertain broker write',
  'Unsupported broker capabilities remain unsupported',
]

export default function SafetyPage() {
  return (
    <div className="page safety-page">
      <PageHeader
        eyebrow="Safety architecture"
        title="When evidence is incomplete, the safe answer is stop."
        lead="The system is designed to narrow authority at every boundary, persist intent before action, and hand ambiguity to an operator instead of guessing."
      />

      <Callout title="Commissioning blocker" tone="danger" icon="!">
        <p>
          Checkpoint 7C remains blocked. Active protective-stop verification
          is unresolved, so the system cannot truthfully classify any
          advertised working state as verified active protection.
        </p>
      </Callout>

      <SafetyLayers />

      <section className="hard-boundaries" aria-labelledby="boundaries-title">
        <div className="section-heading">
          <span className="section-label">Non-negotiable constraints</span>
          <h2 id="boundaries-title">Hard boundaries</h2>
          <p>
            These controls either reject a plan, halt progress, or require
            fresh evidence and operator resolution.
          </p>
        </div>
        <ul>
          {boundaries.map((boundary) => (
            <li key={boundary}>
              <span aria-hidden="true">✓</span>
              {boundary}
            </li>
          ))}
        </ul>
      </section>

      <ProtectionStateMap />

      <aside className="semantic-warning" aria-labelledby="semantic-title">
        <span aria-hidden="true">!</span>
        <div>
          <h2 id="semantic-title">Words do not prove outcomes</h2>
          <p>A review response is not an order.</p>
          <p>A submitted order is not necessarily a fill.</p>
          <p>A cancel request accepted is not cancellation completed.</p>
        </div>
      </aside>
    </div>
  )
}
