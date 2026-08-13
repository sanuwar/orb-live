import { act, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import App from '../App'
function navigate(route) { act(() => { window.location.hash = `#/${route}`; window.dispatchEvent(new HashChangeEvent('hashchange')) }) }
describe('M25.1 parent routes and compatibility', () => {
  it('retains journal, records, manual record, and evidence import routes', () => {
    render(<App />)
    ;['journal', 'journal-records', 'manual-record', 'evidence-import'].forEach((route) => { navigate(route); expect(document.querySelector('.page')).toBeInTheDocument() })
  })
  it('retains the Build Journey compatibility alias', () => {
    render(<App />); navigate('development-journey')
    expect(screen.getByRole('heading', { level: 1, name: /one bounded system/i })).toBeVisible()
  })
  it('keeps Operations bounded to local watcher controls', () => {
    render(<App />); navigate('operations')
    expect(screen.getByRole('button', { name: 'Start morning watch' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Stop watch' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: /trade|broker/i })).not.toBeInTheDocument()
    expect(screen.getByText(/never contacts market-data or broker services/i)).toBeVisible()
  })
})
