import { act, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import App from '../App'

const destinations = [
  ['overview', /disciplined opening-range system/i],
  ['strategy', /wait for the range/i],
  ['system-flow', /decision is not an authorization/i],
  ['safety', /safe answer is stop/i],
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

  it('marks the active destination', () => {
    render(<App />)
    navigate('system-flow')

    const activeLinks = screen.getAllByRole('link', { name: 'System Flow' })
    activeLinks.forEach((link) => {
      expect(link).toHaveAttribute('aria-current', 'page')
    })
  })
})
