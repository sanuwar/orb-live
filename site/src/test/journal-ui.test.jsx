import { act, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import App from '../App'
import { createManualRecord } from '../journal/journalModel'
import { createJournalStore } from '../journal/journalStore'

function memoryStorage() { const data = new Map(); return { getItem: (key) => data.get(key) ?? null, setItem: (key, value) => data.set(key, value) } }
function storeWithRecords() {
  const store = createJournalStore(memoryStorage())
  store.append(createManualRecord({ recordType: 'MANUAL_RECOVERY', symbol: 'ZZTEST', side: 'SELL', quantity: '1', eventTimestamp: '2026-01-01T10:00', status: 'VERIFIED_FLAT', runId: 'fake-run-ui', note: 'Sanitized recovery' }, { submissionKey: 'ui-recovery' }))
  return store
}
function navigate(route) { act(() => { window.location.hash = `#/${route}`; window.dispatchEvent(new HashChangeEvent('hashchange')) }) }

describe('trade journal workflow', () => {
  it('renders a clear empty overview', () => { render(<App journalStore={createJournalStore(memoryStorage())} />); navigate('journal'); expect(screen.getByRole('heading', { name: 'No journal records yet' })).toBeVisible(); expect(screen.getByText(/not a broker execution interface/i)).toBeVisible() })
  it('renders summary, filters, and a run detail timeline', async () => {
    const store = storeWithRecords(); render(<App journalStore={store} />); navigate('journal'); expect(screen.getByText('Verified-flat runs').previousSibling).toHaveTextContent('1')
    navigate('journal-records'); expect(screen.getByText('1 record shown')).toBeVisible(); fireEvent.change(screen.getByLabelText('Run ID'), { target: { value: 'other-run' } }); expect(screen.getByRole('heading', { name: 'No matching records' })).toBeVisible()
    fireEvent.change(screen.getByLabelText('Run ID'), { target: { value: 'fake-run-ui' } }); await userEvent.click(screen.getByRole('link', { name: 'View timeline' })); expect(screen.getByRole('heading', { name: 'Complete event timeline' })).toBeVisible(); expect(screen.getByText(/Initiator: OPERATOR/)).toBeVisible()
  })
  it('creates a validated manual trade', async () => {
    const user = userEvent.setup(); const store = createJournalStore(memoryStorage()); render(<App journalStore={store} />); navigate('manual-record')
    await user.type(screen.getByLabelText('Symbol'), 'aapl'); await user.type(screen.getByLabelText('Quantity'), '2.50'); fireEvent.change(screen.getByLabelText('Date and time'), { target: { value: '2026-01-02T10:30' } }); await user.type(screen.getByLabelText('Execution price (optional)'), '12.25'); await user.click(screen.getByRole('button', { name: 'Save journal record' }))
    expect(screen.getByText('Manual journal record saved.')).toBeVisible(); expect(store.discover()[0]).toMatchObject({ source: 'MANUAL', symbol: 'AAPL', quantity: '2.5', executionPrice: '12.25' })
  })
  it('shows safe validation errors and never renders script markup', async () => {
    const user = userEvent.setup(); render(<App journalStore={createJournalStore(memoryStorage())} />); navigate('manual-record'); await user.type(screen.getByLabelText('Symbol'), 'bad<script>'); await user.type(screen.getByLabelText('Quantity'), '0'); fireEvent.change(screen.getByLabelText('Date and time'), { target: { value: '2026-01-02T10:30' } }); await user.click(screen.getByRole('button', { name: 'Save journal record' })); expect(document.querySelector('script')).not.toBeInTheDocument(); expect(screen.getAllByText(/invalid|greater than zero|unsafe/i).length).toBeGreaterThan(0)
  })
  it('describes local evidence import without execution controls', () => { render(<App journalStore={createJournalStore(memoryStorage())} />); navigate('evidence-import'); expect(screen.getByText(/never runs a launcher/i)).toBeVisible(); expect(screen.queryByRole('button', { name: /place|cancel|review order/i })).not.toBeInTheDocument() })
})
