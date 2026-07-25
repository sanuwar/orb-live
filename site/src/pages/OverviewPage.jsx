import Callout from '../components/Callout'
import JourneySpine from '../components/JourneySpine'
import PageHeader from '../components/PageHeader'

export default function OverviewPage() {
  return (
    <div className="page">
      <PageHeader
        eyebrow="Internal system explainer"
        title="A disciplined opening-range system, designed around proof."
        lead="ORB-LIVE turns a fixed market observation into a progressively narrower, risk-bounded, evidence-driven workflow."
      >
        <div className="hero-actions">
          <a className="primary-button" href="#/strategy">
            See the measurements
            <span aria-hidden="true">→</span>
          </a>
          <span className="creator-line">Created by <strong>Sanuwar Rashid</strong></span>
        </div>
      </PageHeader>

      <Callout title="Controlled commissioning" tone="warning" icon="!">
        <p>
          Strategy execution remains disabled. Checkpoint 7C is blocked, and
          active protective-stop verification remains unresolved.
        </p>
      </Callout>

      <JourneySpine
        title="5 stages · 15 operating numbers"
        description="The dashboard now leads with thresholds, limits, timing, and current proof status."
      />

      <section className="comparison-section" aria-labelledby="scope-title">
        <div className="section-heading">
          <span className="section-label">Scope</span>
          <h2 id="scope-title">Documentation—not a trading console</h2>
        </div>
        <div className="comparison-grid">
          <article className="comparison-card comparison-does">
            <span className="comparison-icon" aria-hidden="true">✓</span>
            <h3>Measures</h3>
            <ul>
              <li>Opening range, gap, volume, and market context</li>
              <li>Risk, position, loss, and time limits</li>
              <li>Durable lifecycle and reconciliation evidence</li>
            </ul>
          </article>
          <article className="comparison-card comparison-does-not">
            <span className="comparison-icon" aria-hidden="true">×</span>
            <h3>Does not</h3>
            <ul>
              <li>Guarantee profitability or capped realized losses</li>
              <li>Authorize automatic or unattended live trading</li>
              <li>Connect this dashboard to broker or account data</li>
            </ul>
          </article>
        </div>
      </section>
    </div>
  )
}