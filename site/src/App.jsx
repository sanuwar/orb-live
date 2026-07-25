import { useEffect, useState } from 'react'
import AppShell from './components/AppShell'
import { navigation } from './data/content'
import OverviewPage from './pages/OverviewPage'
import StrategyPage from './pages/StrategyPage'
import SystemFlowPage from './pages/SystemFlowPage'
import SafetyPage from './pages/SafetyPage'
import DevelopmentJourneyPage from './pages/DevelopmentJourneyPage'
import ArchitectureGlossaryPage from './pages/ArchitectureGlossaryPage'

const pages = {
  overview: OverviewPage,
  strategy: StrategyPage,
  'system-flow': SystemFlowPage,
  safety: SafetyPage,
  'development-journey': DevelopmentJourneyPage,
  'architecture-glossary': ArchitectureGlossaryPage,
}

function currentRoute() {
  const route = window.location.hash.replace(/^#\/?/, '')
  return pages[route] ? route : 'overview'
}

export default function App() {
  const [route, setRoute] = useState(currentRoute)

  useEffect(() => {
    const handleHashChange = () => {
      setRoute(currentRoute())
      window.scrollTo({ top: 0, behavior: 'auto' })
    }

    window.addEventListener('hashchange', handleHashChange)
    return () => window.removeEventListener('hashchange', handleHashChange)
  }, [])

  const Page = pages[route]
  const pageLabel = navigation.find((item) => item.slug === route)?.label

  return (
    <AppShell route={route}>
      <Page />
      <span className="sr-only" aria-live="polite">
        Current page: {pageLabel}
      </span>
    </AppShell>
  )
}
