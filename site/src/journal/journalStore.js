export const STORAGE_KEY = 'orb-live.trade-journal.v1'
const INDEXES = ['journalRecordId', 'submissionKey', 'sanitizedImportKey']

export function createJournalStore(storage = globalThis.localStorage) {
  function read() {
    try {
      const value = JSON.parse(storage?.getItem(STORAGE_KEY) ?? '[]')
      return Array.isArray(value) ? value.map((record) => Object.freeze({ ...record })) : []
    } catch { return [] }
  }
  function appendMany(candidates) {
    const all = read()
    const indexes = new Map(INDEXES.map((key) => [key, new Set(all.map((record) => record[key]).filter(Boolean))]))
    const records = []
    let duplicates = 0
    for (const candidate of candidates) {
      if (INDEXES.some((key) => candidate[key] && indexes.get(key).has(candidate[key]))) { duplicates += 1; continue }
      const record = Object.freeze({ ...candidate })
      all.push(record); records.push(record)
      INDEXES.forEach((key) => { if (record[key]) indexes.get(key).add(record[key]) })
    }
    if (records.length) storage?.setItem(STORAGE_KEY, JSON.stringify(all))
    return Object.freeze({ appended: records.length, duplicates, records: Object.freeze(records) })
  }
  return Object.freeze({ discover: () => Object.freeze(read()), append: (record) => appendMany([record]), appendMany })
}
