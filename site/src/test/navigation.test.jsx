import { act, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import App from '../App'

const destinations = [
  ['overview', /disciplined opening-range system/i],
  ['strategy', /15 symbols/i],
  ['system-flow', /15 → 0–1/i],
  ['safety', /risk/i],
  ['development-journey', /closing one uncertainty/i],
  ['architecture-glossary', /narrow layers/i],
]

function navigate(route) {
  act(() => {
    window.location.hash = `#/${route}`
    window.dispatchEvent(new HashChangeEvent('hashchange'))
  })
}

describe('hash navigation', () => {
  destinations.forEach(([route, heading]) => {
    it(`renders the ${route} destination`, () => {
      render(<App />)
      navigate(route)
      expect(screen.getByRole('heading', { level: 1, name: heading })).toBeVisible()
    })
  })

  it('groups the technical system flow into five shared chapters', () => {
    render(<App />)
    navigate('system-flow')

    expect(document.querySelectorAll('.system-equation > ol > li')).toHaveLength(5)
    expect(screen.getByText('$15 ∩ $3k ∩ 1')).toBeVisible()
  })

  it('renders eight captured strategy measurements', () => {
    render(<App />)
    navigate('strategy')

    expect(document.querySelectorAll('.measurement-group > ol > li')).toHaveLength(8)
    expect(screen.getByText('≥3.0')).toBeVisible()
    expect(screen.getByText('09:40–11:00')).toBeVisible()
  })

  it('renders fourteen explicit safety limits', () => {
    render(<App />)
    navigate('safety')

    expect(document.querySelectorAll('.limit-groups dd')).toHaveLength(14)
    expect(screen.getByText('blind retries after uncertainty')).toBeVisible()
  })

  it('marks the active destination', () => {
    render(<App />)
    navigate('system-flow')

    const activeLinks = screen.getAllByRole('link', { name: 'System Flow' })
    activeLinks.forEach((link) => {
      expect(link).toHaveAttribute('aria-current', 'page')
    })
  })
})
