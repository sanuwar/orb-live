import { useCallback, useEffect, useMemo, useState } from 'react'
import { JournalContext } from './journalContext'
import { importSanitizedEvidence } from './evidenceImporter'
import { createManualRecord } from './journalModel'
import { createJournalStore } from './journalStore'
import { parseAutomaticProjection } from './runtimeProjection'
import { readAutomaticRuntimeProjection } from '../operations/localControlClient'

export function JournalProvider({ children, store: suppliedStore }) {
  const store = useMemo(() => suppliedStore ?? createJournalStore(), [suppliedStore])
  const [records, setRecords] = useState(() => store.discover())
  const [automatic, setAutomatic] = useState({ status: 'NO_AUTOMATIC_EVIDENCE', sessions: [], records: [], events: [] })
  useEffect(() => {
    let active = true
    readAutomaticRuntimeProjection().then((result) => { if (active) setAutomatic(parseAutomaticProjection(result.payload)) })
    return () => { active = false }
  }, [])
  const addManualRecord = useCallback((input, options) => {
    const result = store.append(createManualRecord(input, options)); setRecords(store.discover()); return result
  }, [store])
  const importEvidence = useCallback(async (text, options) => {
    const result = await importSanitizedEvidence(text, store, options); setRecords(store.discover()); return result
  }, [store])
  const value = useMemo(() => ({ records: [...automatic.records, ...records], manualRecords: records, automatic, addManualRecord, importEvidence }), [records, automatic, addManualRecord, importEvidence])
  return <JournalContext.Provider value={value}>{children}</JournalContext.Provider>
}
