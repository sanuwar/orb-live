export default function JournalBadge({ children, tone = 'neutral' }) {
  return <span className={`journal-badge journal-badge-${tone}`}>{children}</span>
}
