import Callout from '../components/Callout'
import JourneySpine from '../components/JourneySpine'
import PageHeader from '../components/PageHeader'
import DecisionFunnel from '../components/diagrams/DecisionFunnel'
import SystemFlowDiagram from '../components/diagrams/SystemFlowDiagram'

export default function SystemFlowPage() {
  return (
    <div className="page">
      <PageHeader
        eyebrow="Separated responsibilities"
        title="A decision is not an authorization. An authorization is not a fill."
        lead="ORB-LIVE is organized as a series of boundaries. Read-only observations become hypothetical decisions; only a separate, explicit process could authorize a durable broker action."
      />

      <JourneySpine
        compact
        title="The complete system journey"
        description="System Flow is where all five chapters connect. Each chapter still stops independently when its evidence is insufficient."
      />

      <SystemFlowDiagram />

      <div className="two-column-diagrams">
        <DecisionFunnel />
        <section className="boundary-explainer" aria-labelledby="boundary-title">
          <span className="section-label">How the thread changes state</span>
          <h2 id="boundary-title">Four different kinds of progress</h2>
          <dl>
            <div><dt>Observed</dt><dd>Data was read and checked for freshness or conflicts.</dd></div>
            <div><dt>Hypothetical</dt><dd>A strategy rule evaluated a possible decision in shadow.</dd></div>
            <div><dt>Reviewed</dt><dd>A bounded proposal or external state was inspected.</dd></div>
            <div><dt>Authorized</dt><dd>An operator approved a specific intent for a short-lived context—not a general permission to trade.</dd></div>
          </dl>
        </section>
      </div>

      <Callout title="Current boundary" tone="warning" icon="!">
        <p>
          The dashboard is not connected to any of these runtime stages.
          Strategy execution remains disabled, and the current commissioning
          state does not authorize a live entry.
        </p>
      </Callout>
    </div>
  )
}