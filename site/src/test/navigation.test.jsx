import { act, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import App from '../App'

const destinations = [
  ['operations', /read-only runtime dashboard/i],
  ['performance', /sanitized journal/i],
  ['strategy-system', /one strategy, bounded/i],
  ['build-journey', /one bounded system/i],
  ['journal', /trade journal/i],
  ['evidence-import', /import/i],
]
function navigate(route) {
  act(() => { window.location.hash = `#/${route}`; window.dispatchEvent(new HashChangeEvent('hashchange')) })
}
describe('hash navigation', () => {
  destinations.forEach(([route, heading]) => {
    it(`renders the ${route} destination`, () => {
      render(<App />); navigate(route)
      expect(screen.getByRole('heading', { level: 1, name: heading })).toBeVisible()
    })
  })
  it('marks the active primary destination', () => {
    render(<App />); navigate('strategy-system')
    screen.getAllByRole('link', { name: 'Strategy & System' }).forEach((link) => expect(link).toHaveAttribute('aria-current', 'page'))
  })
  it('retains detailed strategy documentation under legacy routes', () => {
    render(<App />); navigate('strategy')
    expect(document.querySelectorAll('.measurement-group > ol > li')).toHaveLength(8)
    expect(screen.getByText('10 symbols. 8 measurements. 80 minutes.')).toBeVisible()
  })
})