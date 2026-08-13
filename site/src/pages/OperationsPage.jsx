import PageHeader from '../components/PageHeader'

const areas = [
  ['Morning readiness', 'Local readiness check is the pre-market entry point. Runtime projection arrives in M25.2.'],
  ['Session status', 'No live session projection is connected in this static surface.'],
  ['Scanner / filters', 'The fixed 10-symbol orb_long_v0 commissioning universe remains bounded.'],
  ['Candidate', 'A genuine LONG_CANDIDATE is the only commissioning input.'],
  ['Trade lifecycle', 'One selected equity lifecycle; no multi-position workflow.'],
  ['Protection', 'Authoritative protection evidence remains separately grounded.'],
  ['Events', 'Automatic runtime projection is planned for M25.4.'],
  ['Artifacts / next action', 'Use the local operator flow and saved sanitized evidence; this page performs no action.'],
]
export default function OperationsPage() {
  return <div className="page">
    <PageHeader eyebrow="Operations" title="Local process, read-only operator context." lead="This is the v1 operations landing surface. It is static in M25.1: no runtime control, broker connection, market-data request, or remote action is available." />
    <section aria-labelledby="operations-areas"><div className="section-heading"><span className="section-label">Operator workspace</span><h2 id="operations-areas">Current operating areas</h2><p>Live projection and local allowlisted controls arrive in later M25 PRs.</p></div><div className="area-card-grid">{areas.map(([title, detail]) => <article key={title}><h3>{title}</h3><p>{detail}</p></article>)}</div></section>
  </div>
}