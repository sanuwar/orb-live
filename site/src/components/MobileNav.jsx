import { navigation } from '../data/content'
import { Brand } from './Sidebar'

export default function MobileNav({ route, open, onToggle, onNavigate }) {
  return (
    <header className="mobile-header">
      <Brand />
      <button
        className="menu-button"
        type="button"
        aria-expanded={open}
        aria-controls="mobile-navigation"
        onClick={onToggle}
      >
        <span className="sr-only">{open ? 'Close menu' : 'Open menu'}</span>
        <span aria-hidden="true" className="menu-lines">
          <i />
          <i />
          <i />
        </span>
      </button>
      <nav
        id="mobile-navigation"
        className="mobile-navigation"
        aria-label="Mobile documentation pages"
        hidden={!open}
      >
        {navigation.map((item) => (
          <a
            key={item.slug}
            href={`#/${item.slug}`}
            className={route === item.slug ? 'active' : undefined}
            aria-current={route === item.slug ? 'page' : undefined}
            onClick={onNavigate}
          >
            <span aria-hidden="true">{item.shortLabel}</span>
            {item.label}
          </a>
        ))}
      </nav>
    </header>
  )
}
