import PageHeader from '../components/PageHeader'
import DevelopmentTimeline from '../components/diagrams/DevelopmentTimeline'

const frontier = [
  'Genuine LONG_CANDIDATE',
  'Candidate commissioning handoff',
  'Production read-only rehearsal',
  'READY_FOR_EXPLICIT_ATTENDED_WRITE_AUTHORIZATION',
  'First explicitly authorized supervised one-share lifecycle',
  'Protection grounding',
  'Exit, reconciliation, and P&L evidence',
  'Activation evidence',
  'Bounded unattended pilot',
  'ORB-LIVE v1',
]

export default function DevelopmentJourneyPage() {
  return (
    <div className="page build-journey-page">
      <PageHeader
        eyebrow="Build journey"
        title="One bounded system, built one verified boundary at a time."
        lead="ORB-LIVE is in live commissioning: a genuine candidate can reach attended review and read-only rehearsal, but no unattended activation or broker write authority is active."
      />

      <section aria-labelledby="current-status-title">
        <div className="section-heading">
          <span className="section-label">Current status</span>
          <h2 id="current-status-title">LIVE COMMISSIONING</h2>
          <p>Engineering-stage estimate: approximately 98%. This is a delivery estimate, not trading-performance or financial-success probability.</p>
        </div>
        <dl className="build-status-grid">
          <div><dt>Current frontier</dt><dd>First supervised one-share live lifecycle</dd></div>
          <div><dt>Authority</dt><dd>ATTENDED_COMMISSIONING_ONLY</dd></div>
          <div><dt>Automated / unattended activation</dt><dd>NOT ACTIVATED</dd></div>
          <div><dt>Strategy</dt><dd>orb_long_v0</dd></div>
          <div><dt>Commissioning universe</dt><dd>10 symbols</dd></div>
        </dl>
      </section>

      <DevelopmentTimeline />

      <section aria-labelledby="frontier-title">
        <div className="section-heading">
          <span className="section-label">Current frontier</span>
          <h2 id="frontier-title">What remains before the v1 deliverable</h2>
          <p>Only the first four steps are current commissioning flow. The remaining steps are future work and are not represented as complete.</p>
        </div>
        <ol className="frontier-flow">
          {frontier.map((step, index) => (
            <li className={index < 4 ? 'frontier-current' : 'frontier-future'} key={step}>
              <span>{String(index + 1).padStart(2, '0')}</span><strong>{step}</strong>
            </li>
          ))}
        </ol>
      </section>

      <section className="v1-boundary" aria-labelledby="v1-boundary-title">
        <div className="section-heading">
          <span className="section-label">ORB-LIVE v1 boundary</span>
          <h2 id="v1-boundary-title">One deliberate lifecycle, not a portfolio system</h2>
        </div>
        <ul>
          <li>ONE strategy</li><li>ONE account</li><li>ONE selected equity position lifecycle</li><li>ONE bounded daily trade attempt</li>
        </ul>
        <p>Future evolution: after successful ORB-LIVE v1 validation, multi-entry and portfolio-level risk management can be developed separately. They are not part of this deliverable.</p>
      </section>
    </div>
  )
}
