import { useState } from 'react'
import PageHeader from '../components/PageHeader'
import { useJournal } from '../journal/journalContext'

const SAFE_CODES = { EVIDENCE_IMPORTED: 'Sanitized evidence imported.', EVIDENCE_ALREADY_IMPORTED: 'This evidence was already imported.', PRIVATE_FIELD_REJECTED: 'Evidence rejected: private-field policy.', MALFORMED_EVIDENCE: 'Evidence rejected: malformed file.', UNSUPPORTED_EVIDENCE: 'Evidence rejected: unsupported schema.', UNSAFE_EVIDENCE_SHAPE: 'Evidence rejected: unsafe shape.', HASH_UNAVAILABLE: 'Evidence rejected: local hashing unavailable.' }
export default function EvidenceImportPage() {
  const { importEvidence } = useJournal(); const [file, setFile] = useState(null); const [message, setMessage] = useState('')
  const submit = async (event) => {
    event.preventDefault(); if (!file) { setMessage('Choose a sanitized JSON file.'); return }
    if (file.size > 1024 * 1024) { setMessage('Evidence rejected: file is too large.'); return }
    try {
      const result = await importEvidence(await file.text())
      setMessage(SAFE_CODES[result.code] ?? 'Evidence import completed with a safe classification.')
    } catch { setMessage('Evidence import failed with a safe local error.') }
  }
  return <div className="page journal-page"><PageHeader eyebrow="Local evidence" title="Import sanitized commissioning evidence" lead="Select a sanitized local JSON file. Importing never runs a launcher, opens a broker session, or sends data over a network." />
    <section className="journal-form-card"><form onSubmit={submit}><label>Sanitized evidence file<input type="file" accept="application/json,.json" onChange={(event) => setFile(event.target.files?.[0] ?? null)} /></label><p>Private fields, raw requests, raw responses, credentials, account data, and authorization material are rejected.</p><button className="primary-button" type="submit">Import local evidence</button>{message && <p role="status" className="form-result">{message}</p>}</form></section>
    <section className="journal-boundary"><strong>Read-only boundary</strong><span>The importer reads only the file you choose, stores normalized local records, and never contacts ORB-LIVE runtime code.</span></section>
  </div>
}
