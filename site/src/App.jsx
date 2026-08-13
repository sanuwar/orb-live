import { useEffect, useState } from 'react'
import AppShell from './components/AppShell'
import { navigation } from './data/content'
import { JournalProvider } from './journal/JournalProvider'
import OverviewPage from './pages/OverviewPage'
import OperationsPage from './pages/OperationsPage'
import PerformancePage from './pages/PerformancePage'
import StrategySystemPage from './pages/StrategySystemPage'
import StrategyPage from './pages/StrategyPage'
import SystemFlowPage from './pages/SystemFlowPage'
import SafetyPage from './pages/SafetyPage'
import DevelopmentJourneyPage from './pages/DevelopmentJourneyPage'
import ArchitectureGlossaryPage from './pages/ArchitectureGlossaryPage'
import JournalOverviewPage from './pages/JournalOverviewPage'
import JournalRecordsPage from './pages/JournalRecordsPage'
import JournalDetailPage from './pages/JournalDetailPage'
import ManualRecordPage from './pages/ManualRecordPage'
import EvidenceImportPage from './pages/EvidenceImportPage'

const pages = {
  operations: OperationsPage, performance: PerformancePage, 'strategy-system': StrategySystemPage,
  'build-journey': DevelopmentJourneyPage,
  overview: OverviewPage, strategy: StrategyPage, 'system-flow': SystemFlowPage, safety: SafetyPage,
  'development-journey': DevelopmentJourneyPage, 'architecture-glossary': ArchitectureGlossaryPage,
  journal: JournalOverviewPage, 'journal-records': JournalRecordsPage, 'manual-record': ManualRecordPage,
  'evidence-import': EvidenceImportPage,
}
function routeState() {
  const raw = window.location.hash.replace(/^#\/?/, '')
  if (raw.startsWith('journal-detail/')) return { route: 'journal-detail', detailKey: raw.slice('journal-detail/'.length) }
  return { route: pages[raw] ? raw : 'overview', detailKey: null }
}
function RoutedApp() {
  const [state, setState] = useState(routeState)
  useEffect(() => {
    const handle = () => { setState(routeState()); window.scrollTo({ top: 0, behavior: 'auto' }) }
    window.addEventListener('hashchange', handle); return () => window.removeEventListener('hashchange', handle)
  }, [])
  const Page = state.route === 'journal-detail' ? JournalDetailPage : pages[state.route]
  const label = navigation.find((item) => item.slug === state.route)?.label ?? 'Journal detail'
  return <AppShell route={state.route}><Page detailKey={state.detailKey} /><span className="sr-only" aria-live="polite">Current page: {label}</span></AppShell>
}
export default function App({ journalStore }) { return <JournalProvider store={journalStore}><RoutedApp /></JournalProvider> }
