const states = [
  {
    name: 'NOT_PROTECTED',
    label: 'Not protected',
    detail: 'Evidence shows required protection is absent or insufficient.',
    tone: 'danger',
  },
  {
    name: 'INDETERMINATE',
    label: 'Indeterminate',
    detail: 'Evidence cannot prove whether valid active protection exists.',
    tone: 'warning',
  },
  {
    name: 'VERIFIED_ACTIVE_PROTECTION',
    label: 'Verified active protection',
    detail: 'Reserved for a directly grounded state-and-fields proof.',
    tone: 'safe',
  },
]

export default function ProtectionStateMap() {
  return (
    <figure
      className="protection-map diagram-card"
      aria-labelledby="protection-map-title"
    >
      <figcaption>
        <span className="section-label">Evidence classification</span>
        <h2 id="protection-map-title">Protection states</h2>
        <p>
          The verified-active mapping is intentionally empty until direct
          evidence establishes an exact predicate.
        </p>
      </figcaption>
      <ol>
        {states.map((state, index) => (
          <li key={state.name} className={`protection-${state.tone}`}>
            <span className="state-marker" aria-hidden="true">
              {index + 1}
            </span>
            <div>
              <code>{state.name}</code>
              <strong>{state.label}</strong>
              <p>{state.detail}</p>
              {state.tone === 'safe' && (
                <span className="empty-state-pill">No grounded mapping yet</span>
              )}
            </div>
          </li>
        ))}
      </ol>
    </figure>
  )
}
