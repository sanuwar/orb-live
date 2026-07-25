import Callout from '../components/Callout'
import JourneySpine from '../components/JourneySpine'
import PageHeader from '../components/PageHeader'
import ProtectionStateMap from '../components/diagrams/ProtectionStateMap'
import SafetyLayers from '../components/diagrams/SafetyLayers'

const boundaryGroups = [
  {
    title: 'Position boundaries',
    stage: 'Authorize',
    items: ['No margin', 'No short selling', 'No averaging down', 'One open position'],
  },
  {
    title: 'Loss boundaries',
    stage: 'Authorize → Protect',
    items: ['Limited daily entries', 'Daily and weekly loss stops', 'Total drawdown halt'],
  },
  {
    title: 'Uncertainty boundaries',
    stage: 'Prove',
    items: [
      'No automatic retry of an uncertain broker write',
      'Unsupported broker capabilities remain unsupported',
    ],
  },
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

      <JourneySpine
        compact
        title="Safety travels with the work"
        description="The controls below attach to the same Observe–Narrow–Authorize–Protect–Prove journey. They are not a second workflow."
      />

      <SafetyLayers />

      <section className="hard-boundaries" aria-labelledby="boundaries-title">
        <div className="section-heading">
          <span className="section-label">Always-on constraints</span>
          <h2 id="boundaries-title">Hard boundaries, grouped by purpose</h2>
          <p>
            These constraints apply across the journey. Grouping them shows
            why they exist without turning them into another nine-step list.
          </p>
        </div>
        <div className="boundary-groups">
          {boundaryGroups.map((group) => (
            <article key={group.title}>
              <span>{group.stage}</span>
              <h3>{group.title}</h3>
              <ul>
                {group.items.map((item) => (
                  <li key={item}><span aria-hidden="true">✓</span>{item}</li>
                ))}
              </ul>
            </article>
          ))}
        </div>
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