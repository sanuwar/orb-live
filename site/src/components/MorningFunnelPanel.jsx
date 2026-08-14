import { useJournal } from '../journal/journalContext'
import { formatEasternTime } from '../formatters/easternTime'
import { morningFunnel } from '../operations/morningFunnel'

function count(value) { return Number.isInteger(value) ? value : 'N/A' }
function gate(value) { return value === 'NOT_EVALUATED' ? 'NOT EVALUATED' : value }

export default function MorningFunnelPanel() {
  const { automatic } = useJournal()
  const current = morningFunnel(automatic)
  if (current.state === 'NO_EVIDENCE') return <section className="morning-funnel operations-unavailable" aria-labelledby="morning-funnel-title"><span className="section-label">Morning funnel / current scan</span><h2 id="morning-funnel-title">FUNNEL EVIDENCE UNAVAILABLE</h2><p>No authentic per-symbol evaluation evidence is available. No survivor state is inferred.</p></section>
  if (current.state === 'STALE') return <section className="morning-funnel operations-unavailable" aria-labelledby="morning-funnel-title"><span className="section-label">Morning funnel / current scan</span><h2 id="morning-funnel-title">STALE</h2><p>The latest funnel evidence is not from the current ET session. It is not presented as current.</p></section>
  if (current.state === 'INCOMPLETE') return <section className="morning-funnel operations-unavailable" aria-labelledby="morning-funnel-title"><span className="section-label">Morning funnel / current scan</span><h2 id="morning-funnel-title">EVIDENCE INCOMPLETE</h2><p>Current-session filter evidence is missing or incomplete. Survivor counts are unavailable rather than inferred.</p></section>

  const { evaluation, funnel } = current
  const facts = evaluation.filterFacts
  const spyLabel = funnel.spyVwapEnabled === true ? 'SPY > VWAP' : funnel.spyVwapEnabled === false ? 'SPY/VWAP not active' : 'SPY/VWAP N/A'
  return <section className="morning-funnel" aria-labelledby="morning-funnel-title">
    <div className="section-heading"><span className="section-label">Morning funnel / current scan</span><h2 id="morning-funnel-title">Latest authentic symbol progression</h2><p>Evaluation {formatEasternTime(evaluation.scheduledAt)}. Dynamic SPY/VWAP and breakout waits remain eligible for later evaluations.</p></div>
    <dl className="funnel-counts">
      <div><dt>Starting symbols</dt><dd>{count(funnel.startingSymbolCount)}</dd></div>
      <div><dt>Gap &gt;= {funnel.gapMinimumPct ?? 'N/A'}%</dt><dd>{count(funnel.survivorsAfterGap)}</dd></div>
      <div><dt>Opening RVOL &gt;= {funnel.openingRvolMinimum ?? 'N/A'}x</dt><dd>{count(funnel.survivorsAfterOpeningRvol)}</dd></div>
      <div><dt>{spyLabel}</dt><dd>{count(funnel.survivorsAfterSpyVwap)}</dd></div>
      <div><dt>Completed-bar ORB breakout</dt><dd>{count(funnel.survivorsAfterBreakout)}</dd></div>
    </dl>
    <div className="funnel-symbol-summary">
      <div><h3>Currently alive</h3><p>{funnel.aliveSymbols.length ? funnel.aliveSymbols.join(', ') : 'NONE'}</p></div>
      <div><h3>Evidence unavailable</h3><p>{funnel.unavailableSymbols.length ? funnel.unavailableSymbols.join(', ') : 'NONE'}</p></div>
    </div>
    <div className="funnel-table-wrap"><table className="funnel-table"><thead><tr><th>Symbol</th><th>Gap</th><th>Opening RVOL</th><th>SPY/VWAP</th><th>ORB breakout</th><th>Current result</th></tr></thead><tbody>{facts.map((fact) => <tr key={fact.symbol}><th scope="row">{fact.symbol}</th><td>{gate(fact.gates.openingGap)}</td><td>{gate(fact.gates.openingRvol)}</td><td>{gate(fact.gates.spyVwap)}</td><td>{gate(fact.gates.orbBreakout)}</td><td>{fact.terminalRejectionReason ? 'REJECTED' : funnel.aliveSymbols.includes(fact.symbol) ? fact.rejectionReason ? `WAITING - ${fact.rejectionReason}` : 'ALIVE' : 'N/A'}</td></tr>)}</tbody></table></div>
    {funnel.rejectedSymbols.length > 0 && <div className="funnel-rejections"><h3>Static-filter rejections</h3><ul>{funnel.rejectedSymbols.map((item) => <li key={item.symbol}><strong>{item.symbol}</strong><span>{item.reason}</span></li>)}</ul></div>}
  </section>
}
