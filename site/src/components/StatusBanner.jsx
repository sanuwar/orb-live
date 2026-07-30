export default function StatusBanner() {
  return (
    <div className="status-banner" role="status">
      <span className="status-badge">Documentation Mode</span>
      <span>No broker connection</span>
      <span className="status-separator" aria-hidden="true">
        •
      </span>
      <span>No live trading controls</span>
    </div>
  )
}
