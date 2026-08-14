import { useEffect } from 'react'
import { useJournal } from '../journal/journalContext'
import { candidateAttention } from '../operations/candidateAttention'
import { formatEasternTime } from '../formatters/easternTime'

function detail(value) { return value ?? 'N/A' }
export default function CandidateAttentionPanel({ evidenceFreshness = 'CURRENT' }) {
  const { automatic } = useJournal()
  const attention = candidateAttention(automatic)
  const state = attention.state === 'ACTIVE' && evidenceFreshness !== 'CURRENT' ? (evidenceFreshness === 'STALE' ? 'STALE' : 'INCOMPLETE') : attention.state
  useEffect(() => { document.title = state === 'ACTIVE' ? `CANDIDATE - ${attention.candidate.symbol} - ORB-LIVE` : 'ORB-LIVE'; return () => { document.title = 'ORB-LIVE' } }, [attention, state])
  const lastEvaluation = attention.session?.evaluations?.at(-1)?.scheduledAt
  if (state === 'ACTIVE') return <section className="candidate-attention candidate-attention-active" aria-labelledby="candidate-status"><span className="section-label">Candidate status</span><h2 id="candidate-status">CANDIDATE DETECTED</h2><p className="candidate-symbol">{attention.candidate.symbol}</p><p>{formatEasternTime(attention.candidate.timestamp)}</p><dl className="operations-facts"><div><dt>Gap</dt><dd>{detail(null)}</dd></div><div><dt>Opening RVOL</dt><dd>{detail(null)}</dd></div><div><dt>ORB breakout</dt><dd>{detail(null)}</dd></div></dl><strong>REVIEW REQUIRED</strong><p>This read-only attention state does not authorize entry, broker action, or commissioning continuation.</p></section>
  if (state === 'NO_CANDIDATE') return <section className="candidate-attention operations-unavailable" aria-labelledby="candidate-status"><span className="section-label">Candidate status</span><h2 id="candidate-status">SESSION COMPLETE - NO CANDIDATE</h2><p>No qualifying candidate was detected in the completed current session.</p></section>
  if (state === 'STALE') return <section className="candidate-attention operations-unavailable" aria-labelledby="candidate-status"><span className="section-label">Candidate status</span><h2 id="candidate-status">CANDIDATE EVIDENCE STALE</h2><p>Older candidate evidence is not treated as an active current-session candidate.</p></section>
  if (state === 'INCOMPLETE') return <section className="candidate-attention operations-unavailable" aria-labelledby="candidate-status"><span className="section-label">Candidate status</span><h2 id="candidate-status">CANDIDATE EVIDENCE INCOMPLETE</h2><p>Candidate evidence is missing required sanitized identity or timestamp fields.</p></section>
  return <section className="candidate-attention" aria-labelledby="candidate-status"><span className="section-label">Candidate status</span><h2 id="candidate-status">WAITING FOR QUALIFYING SETUP</h2><p>No qualifying candidate has been detected in the current session.</p>{attention.session && <dl className="operations-facts"><div><dt>Evaluation count</dt><dd>{attention.session.evaluationCount}</dd></div><div><dt>Last evaluation</dt><dd>{lastEvaluation ? formatEasternTime(lastEvaluation) : 'N/A'}</dd></div></dl>}</section>
}
