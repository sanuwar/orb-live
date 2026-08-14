import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import DecisionFunnel from '../components/diagrams/DecisionFunnel'
import StrategyPage from '../pages/StrategyPage'

describe('truthful strategy presentation', () => {
  it('shows the current enabled SPY/VWAP requirement and canonical gap/RVOL/breakout facts', () => { render(<DecisionFunnel />); expect(screen.getAllByText(/Gap >= 1.0% vs prior close/).length).toBeGreaterThan(0); expect(screen.getByText(/Opening-range RVOL >= 3.0x vs 20-session median/)).toBeVisible(); expect(screen.getByText(/SPY > VWAP/)).toBeVisible(); expect(screen.getByText(/Completed-bar close > opening-range high/)).toBeVisible(); expect(screen.queryByText(/1m/)).not.toBeInTheDocument() })
  it('separates illustrative examples and research from active strategy logic', () => { render(<StrategyPage />); expect(screen.getByText(/Illustrative example - not live trade data/i)).toBeVisible(); expect(screen.getByText(/REAL CASE STUDY PENDING SUFFICIENT EVIDENCE/)).toBeVisible(); expect(screen.getByText(/Research is not active strategy logic/)).toBeVisible(); expect(screen.queryByText(/ranking weights/i)).not.toBeInTheDocument() })
})