import { safetyLimits } from '../data/content'

const groupOrder = [
  'Default state',
  'Authority',
  'Trade limits',
  'Exposure limits',
  'Loss limits',
  'Prohibited',
  'Current proof',
]

export default function LimitBoard() {
  return (
    <figure className="limit-board" aria-labelledby="limit-board-title">
      <figcaption>
        <span className="section-label">Configured limit board</span>
        <h2 id="limit-board-title">14 values that stop or constrain progress</h2>
        <p>Values are limits—not guarantees of realized market outcomes.</p>
      </figcaption>
      <div className="limit-groups">
        {groupOrder.map((group) => {
          const limits = safetyLimits.filter((limit) => limit.group === group)
          return (
            <section key={group}>
              <h3>{group}</h3>
              <dl>
                {limits.map((limit) => (
                  <div key={limit.label}>
                    <dt>{limit.value}</dt>
                    <dd>{limit.label}</dd>
                  </div>
                ))}
              </dl>
            </section>
          )
        })}
      </div>
    </figure>
  )
}