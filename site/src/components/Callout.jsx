export default function Callout({
  title,
  children,
  tone = 'info',
  icon = 'i',
}) {
  return (
    <aside className={`callout callout-${tone}`}>
      <span className="callout-icon" aria-hidden="true">
        {icon}
      </span>
      <div>
        <strong>{title}</strong>
        <div>{children}</div>
      </div>
    </aside>
  )
}
