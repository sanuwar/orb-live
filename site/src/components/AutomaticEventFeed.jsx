import { useJournal } from '../journal/journalContext'

export default function AutomaticEventFeed() {
  const { automatic } = useJournal()
  if (automatic.status !== 'AUTOMATIC_EVIDENCE_AVAILABLE') return <section className="operations-unavailable"><span className="section-label">Events</span><h2>NO AUTOMATIC EVIDENCE</h2><p>No sanitized runtime event history is available from the local projection.</p></section>
  return <section aria-labelledby="automatic-events"><div className="section-heading"><span className="section-label">Events</span><h2 id="automatic-events">Recent runtime events</h2></div><dl className="operations-facts">{automatic.events.slice(0, 6).map((event) => <div key={event.eventId}><dt>{event.severity} · {event.component}</dt><dd>{event.eventType} · {event.occurredAt}</dd></div>)}</dl></section>
}
