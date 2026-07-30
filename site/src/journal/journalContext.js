import { createContext, useContext } from 'react'
export const JournalContext = createContext(null)
export function useJournal() {
  const value = useContext(JournalContext)
  if (!value) throw new Error('JOURNAL_PROVIDER_REQUIRED')
  return value
}
