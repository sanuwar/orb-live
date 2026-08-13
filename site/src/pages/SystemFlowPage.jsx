import Callout from '../components/Callout'
import PageHeader from '../components/PageHeader'
import DecisionFunnel from '../components/diagrams/DecisionFunnel'
import SystemFlowDiagram from '../components/diagrams/SystemFlowDiagram'

export default function SystemFlowPage() {
  return (
    <div className="page">
      <PageHeader
        eyebrow="Quantified system flow"
        title="55 → 0–5 → 55s → 5R → evidence"
        lead="Each stage transforms measured input into a smaller, bounded, durable output."
      />

      <SystemFlowDiagram />

      <DecisionFunnel />

      <section className="semantic-equations" aria-labelledby="semantics-title">
        <div className="section-heading">
          <span className="section-label">Outcome math</span>
          <h2 id="semantics-title">Three inequalities prevent false certainty</h2>
        </div>
        <div>
          <article><strong>REVIEW</strong><span>≠</span><b>ORDER</b></article>
          <article><strong>SUBMIT</strong><span>≠</span><b>FILL</b></article>
          <article><strong>CANCEL ACCEPTED</strong><span>≠</span><b>CANCELLED</b></article>
        </div>
      </section>

      <Callout title="Current executable output: 0" tone="warning" icon="!">
        <p>Strategy execution is disabled, and Checkpoint 7C remains blocked.</p>
      </Callout>
    </div>
  )
}