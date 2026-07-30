import { useCallback, useMemo, useState } from 'react'
import { JournalContext } from './journalContext'
import { importSanitizedEvidence } from './evidenceImporter'
import { createManualRecord } from './journalModel'
import { createJournalStore } from './journalStore'

export function JournalProvider({ children, store: suppliedStore }) {
  const store = useMemo(() => suppliedStore ?? createJournalStore(), [suppliedStore])
  const [records, setRecords] = useState(() => store.discover())
  const addManualRecord = useCallback((input, options) => {
    const result = store.append(createManualRecord(input, options)); setRecords(store.discover()); return result
  }, [store])
  const importEvidence = useCallback(async (text, options) => {
    const result = await importSanitizedEvidence(text, store, options); setRecords(store.discover()); return result
  }, [store])
  const value = useMemo(() => ({ records, addManualRecord, importEvidence }), [records, addManualRecord, importEvidence])
  return <JournalContext.Provider value={value}>{children}</JournalContext.Provider>
}
