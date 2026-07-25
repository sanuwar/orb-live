import Callout from '../components/Callout'
import JourneySpine from '../components/JourneySpine'
import LimitBoard from '../components/LimitBoard'
import PageHeader from '../components/PageHeader'
import ProtectionStateMap from '../components/diagrams/ProtectionStateMap'

export default function SafetyPage() {
  return (
    <div className="page safety-page">
      <PageHeader
        eyebrow="Quantified safety"
        title="$15 risk. $3,000 cap. 1 position. 0 blind retries."
        lead="Safety is expressed as explicit limits, prohibited counts, and proof states."
      />

      <Callout title="Verified active-protection mappings: 0" tone="danger" icon="0">
        <p>Checkpoint 7C remains blocked. Active protective-stop verification is unresolved; the mapping stays at zero until directly grounded.</p>
      </Callout>

      <JourneySpine
        compact
        title="Limits travel with every stage"
        description="The same operating numbers constrain observation, qualification, authority, exposure, and proof."
      />

      <LimitBoard />

      <ProtectionStateMap />

      <aside className="semantic-warning" aria-labelledby="semantic-title">
        <span aria-hidden="true">≠</span>
        <div>
          <h2 id="semantic-title">Evidence semantics</h2>
          <p>Review ≠ order</p>
          <p>Submit ≠ fill</p>
          <p>Cancel accepted ≠ cancelled</p>
        </div>
      </aside>
    </div>
  )
}