import { act, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import App from '../App'

function navigate(route) {
  act(() => {
    window.location.hash = `#/${route}`
    window.dispatchEvent(new HashChangeEvent('hashchange'))
  })
}

describe('Build Journey', () => {
  it('renders the read-only Build Journey route and navigation tab', () => {
    render(<App />)
    navigate('build-journey')

    expect(screen.getByRole('heading', { level: 1, name: /one bounded system/i })).toBeVisible()
    expect(screen.getAllByRole('link', { name: 'Build Journey' }).length).toBeGreaterThanOrEqual(1)
  })

  it('renders the verified milestone phases and M20 through M24', () => {
    render(<App />)
    navigate('build-journey')

    ;['Foundation', 'Real market observation', 'Safety and broker control', 'Unattended architecture', 'Live commissioning'].forEach((phase) => {
      expect(screen.getByRole('heading', { name: phase })).toBeVisible()
    })
    ;['M20', 'M21', 'M22', 'M23', 'M24'].forEach((milestone) => {
      expect(screen.getByText(milestone)).toBeVisible()
    })
  })

  it('states the current frontier, authority, activation truth, and v1 boundary', () => {
    render(<App />)
    navigate('build-journey')

    expect(screen.getByText('ATTENDED_COMMISSIONING_ONLY')).toBeVisible()
    expect(screen.getByText('NOT ACTIVATED')).toBeVisible()
    expect(screen.getByText('READY_FOR_EXPLICIT_ATTENDED_WRITE_AUTHORIZATION')).toBeVisible()
    expect(screen.getByText('ONE bounded daily trade attempt')).toBeVisible()
    expect(screen.getByText(/Engineering-stage estimate: approximately 98%/)).toBeVisible()
  })
})
