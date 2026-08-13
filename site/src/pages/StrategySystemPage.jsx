import PageHeader from '../components/PageHeader'
const sections = [
  ['Strategy', 'orb_long_v0 is the sole v1 strategy. The commissioning universe is fixed at 10 symbols.', '#/strategy'],
  ['System flow', 'Observation, qualification, review, and evidence remain explicitly bounded.', '#/system-flow'],
  ['Safety', 'Authority remains ATTENDED_COMMISSIONING_ONLY. Automated and unattended activation are not active.', '#/safety'],
  ['Architecture & glossary', 'Static documentation explains narrow layers and shared vocabulary without runtime imports.', '#/architecture-glossary'],
]
export default function StrategySystemPage() {
  return <div className="page">
    <PageHeader eyebrow="Strategy & system" title="One strategy, bounded by explicit system and safety layers." lead="This parent surface centralizes current v1 presentation facts. Detailed legacy views remain available as internal documentation sections." />
    <section aria-labelledby="system-sections"><div className="section-heading"><span className="section-label">Read-only documentation</span><h2 id="system-sections">Strategy and system sections</h2></div><div className="area-card-grid">{sections.map(([title, detail, href]) => <article key={title}><h3>{title}</h3><p>{detail}</p><a href={href}>Open {title}</a></article>)}</div></section>
  </div>
}