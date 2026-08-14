import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import App from '../App'

function load(route) { window.location.hash = `#/${route}` }
describe('parent child route hierarchy', () => {
  it('keeps Performance active and offers a direct parent link on a detail route', async () => { load('journal'); render(<App />); expect(screen.getByRole('navigation', { name: 'Page hierarchy' })).toHaveTextContent('Back to Performance/Journal overview'); const parent = screen.getByRole('link', { name: 'Back to Performance' }); expect(document.querySelector('.sidebar-nav a[href="#/performance"]')).toHaveClass('active'); await userEvent.click(parent); expect(window.location.hash).toBe('#/performance') })
  it('renders Strategy and System hierarchy on a direct-loaded child route', () => { load('strategy'); render(<App />); expect(screen.getByRole('link', { name: 'Back to Strategy & System' })).toBeVisible(); expect(document.querySelector('.sidebar-nav a[href="#/strategy-system"]')).toHaveClass('active') })
  it('retains exactly four primary navigation items', () => { load('performance'); render(<App />); expect(document.querySelectorAll('.sidebar-nav > li')).toHaveLength(4) })
})