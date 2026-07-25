const states = [
  {
    value: '0×',
    name: 'NOT_PROTECTED',
    label: 'Required coverage absent',
    detail: 'Covered quantity is insufficient or required protection is missing.',
    tone: 'danger',
  },
  {
    value: '?',
    name: 'INDETERMINATE',
    label: 'Evidence unresolved',
    detail: 'Observed fields cannot prove valid active protection.',
    tone: 'warning',
  },
  {
    value: '0',
    name: 'VERIFIED_ACTIVE_PROTECTION',
    label: 'Grounded mappings',
    detail: 'No broker state-and-fields predicate is currently approved.',
    tone: 'safe',
  },
]

export default function ProtectionStateMap() {
  return (
    <figure className="protection-map diagram-card" aria-labelledby="protection-map-title">
      <figcaption>
        <span className="section-label">Protection proof</span>
        <h2 id="protection-map-title">0 grounded active-state mappings</h2>
        <p>Unknown stays unknown until direct evidence establishes an exact predicate.</p>
      </figcaption>
      <ol>
        {states.map((state) => (
          <li key={state.name} className={`protection-${state.tone}`}>
            <span className="state-value">{state.value}</span>
            <code>{state.name}</code>
            <strong>{state.label}</strong>
            <p>{state.detail}</p>
            {state.tone === 'safe' && <span className="empty-state-pill">No grounded mapping yet</span>}
          </li>
        ))}
      </ol>
    </figure>
  )
}