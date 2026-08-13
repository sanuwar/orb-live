import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import App from '../App'

describe('M25.1 information architecture', () => {
  it('renders exactly four primary navigation destinations', () => {
    render(<App />)
    const labels = ['Operations', 'Performance', 'Strategy & System', 'Build Journey']
    labels.forEach((label) => expect(screen.getAllByRole('link', { name: label }).length).toBeGreaterThanOrEqual(1))
    expect(document.querySelectorAll('.sidebar-nav > li')).toHaveLength(4)
  })

  it('shows the persistent documentation-only status', () => {
    render(<App />)
    const banner = screen.getByRole('status')
    expect(banner).toHaveTextContent('Documentation Mode')
    expect(banner).toHaveTextContent('No broker connection')
    expect(banner).toHaveTextContent('No live trading controls')
  })

  it('makes the mobile primary navigation operable', async () => {
    const user = userEvent.setup()
    render(<App />)
    const menu = screen.getByRole('button', { name: 'Open menu' })
    await user.click(menu)
    const mobileNav = screen.getByRole('navigation', { name: 'Mobile documentation pages' })
    expect(mobileNav.querySelectorAll('a')).toHaveLength(4)
    await user.click(Array.from(mobileNav.querySelectorAll('a')).find((link) => link.textContent.includes('Performance')))
    expect(menu).toHaveAttribute('aria-expanded', 'false')
  })
})