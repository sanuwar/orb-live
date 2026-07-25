import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import App from '../App'

describe('documentation dashboard', () => {
  it('renders all six navigation destinations', () => {
    render(<App />)

    const labels = [
      'Overview',
      'Strategy',
      'System Flow',
      'Safety',
      'Development Journey',
      'Architecture & Glossary',
    ]

    labels.forEach((label) => {
      expect(
        screen.getAllByRole('link', { name: label }).length,
      ).toBeGreaterThanOrEqual(1)
    })
  })

  it('uses the same five-stage narrative spine', () => {
    render(<App />)

    const spine = document.querySelector('.journey-spine')
    expect(spine).toBeInTheDocument()
    ;['Observe', 'Narrow', 'Authorize', 'Protect', 'Prove'].forEach((stage) => {
      expect(within(spine).getByText(stage)).toBeVisible()
    })
  })

  it('shows the persistent documentation-only status', () => {
    render(<App />)

    const banner = screen.getByRole('status')
    expect(banner).toHaveTextContent('Documentation Mode')
    expect(banner).toHaveTextContent('No broker connection')
    expect(banner).toHaveTextContent('No live trading controls')
  })

  it('shows creator attribution with a safely opened portfolio link', () => {
    render(<App />)

    const link = screen.getByRole('link', { name: 'Sanuwar Rashid' })
    expect(link).toHaveAttribute('href', 'https://sanuwar.github.io')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noreferrer')
  })

  it('makes the mobile navigation operable', async () => {
    const user = userEvent.setup()
    render(<App />)

    const menu = screen.getByRole('button', { name: 'Open menu' })
    expect(menu).toHaveAttribute('aria-expanded', 'false')

    await user.click(menu)
    expect(screen.getByRole('button', { name: 'Close menu' })).toHaveAttribute(
      'aria-expanded',
      'true',
    )

    const mobileNav = screen.getByRole('navigation', {
      name: 'Mobile documentation pages',
    })
    await user.click(
      Array.from(mobileNav.querySelectorAll('a')).find(
        (link) => link.textContent.includes('Safety'),
      ),
    )
    expect(menu).toHaveAttribute('aria-expanded', 'false')
  })

  it('contains the repository test count with historical context', () => {
    render(<App />)

    expect(screen.getByText('1,609')).toBeInTheDocument()
    expect(
      screen.getByText('At the time this dashboard was created'),
    ).toBeInTheDocument()
  })
})
