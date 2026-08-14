export const strategyPresentation = Object.freeze({
  watchlist: '10 symbols',
  openingRange: '5-minute opening range',
  gap: 'Gap >= 1.0% vs prior close',
  rvol: 'Opening-range RVOL >= 3.0x vs 20-session median',
  spyVwapEnabled: true,
  spyVwap: 'SPY > VWAP',
  breakout: 'Completed-bar close > opening-range high',
  riskEnvelope: '$15 planned risk - $3k position cap - 1 open position',
})

export const activeSignalConditions = Object.freeze([
  strategyPresentation.gap,
  strategyPresentation.rvol,
  ...(strategyPresentation.spyVwapEnabled ? [strategyPresentation.spyVwap] : []),
  strategyPresentation.breakout,
])