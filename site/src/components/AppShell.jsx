import { useState } from 'react'
import MobileNav from './MobileNav'
import Sidebar from './Sidebar'
import StatusBanner from './StatusBanner'

export default function AppShell({ route, children }) {
  const [menuOpen, setMenuOpen] = useState(false)

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <Sidebar route={route} />
      <div className="app-frame">
        <MobileNav
          route={route}
          open={menuOpen}
          onToggle={() => setMenuOpen((value) => !value)}
          onNavigate={() => setMenuOpen(false)}
        />
        <StatusBanner />
        <main id="main-content" className="main-content" tabIndex="-1">
          {children}
        </main>
        <footer className="site-footer">
          <p>
            Designed and engineered by{' '}
            <a
              href="https://sanuwar.github.io"
              target="_blank"
              rel="noreferrer"
            >
              Sanuwar Rashid
            </a>
          </p>
          <p>Internal documentation · Not financial advice</p>
        </footer>
      </div>
    </div>
  )
}
