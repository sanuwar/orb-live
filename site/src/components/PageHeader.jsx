export default function PageHeader({ eyebrow, title, lead, children }) {
  return (
    <header className="page-header">
      <span className="eyebrow">{eyebrow}</span>
      <h1>{title}</h1>
      <p className="page-lead">{lead}</p>
      {children}
    </header>
  )
}
