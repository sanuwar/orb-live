export default function MetricCard({ value, label, note }) {
  return (
    <article className="metric-card">
      <strong>{value}</strong>
      <span>{label}</span>
      {note && <small>{note}</small>}
    </article>
  )
}
