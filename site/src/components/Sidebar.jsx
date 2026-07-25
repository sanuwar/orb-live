import { navigation } from '../data/content'

function Brand() {
  return (
    <div className="brand">
      <span className="brand-mark" aria-hidden="true">
        <svg viewBox="0 0 32 32" role="img">
          <path d="M5 22.5 11.5 16l4 4L27 8.5" />
          <path d="M20 8.5h7v7" />
        </svg>
      </span>
      <span>
        <strong>ORB-LIVE</strong>
        <small>System explainer</small>
      </span>
    </div>
  )
}

export default function Sidebar({ route }) {
  return (
    <aside className="sidebar" aria-label="Primary">
      <Brand />
      <nav aria-label="Documentation pages">
        <ol className="sidebar-nav">
          {navigation.map((item) => (
            <li key={item.slug}>
              <a
                href={`#/${item.slug}`}
                className={route === item.slug ? 'active' : undefined}
                aria-current={route === item.slug ? 'page' : undefined}
              >
                <span aria-hidden="true">{item.shortLabel}</span>
                {item.label}
              </a>
            </li>
          ))}
        </ol>
      </nav>
      <div className="sidebar-note">
        <span className="pulse-dot" aria-hidden="true" />
        <div>
          <strong>Commissioning</strong>
          <small>Strategy execution disabled</small>
        </div>
      </div>
    </aside>
  )
}

export { Brand }
