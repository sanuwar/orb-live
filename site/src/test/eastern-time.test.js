import { describe, expect, it } from 'vitest'
import { formatEasternTime } from '../formatters/easternTime'

describe('operator eastern timestamp formatting', () => {
  it('converts UTC to EDT without changing the raw evidence value', () => { const raw = '2026-08-14T02:19:50.487667Z'; expect(formatEasternTime(raw)).toBe('Aug 13, 2026 · 10:19:50 PM ET'); expect(raw).toBe('2026-08-14T02:19:50.487667Z') })
  it('converts UTC to EST in winter', () => expect(formatEasternTime('2026-01-15T15:30:00Z')).toBe('Jan 15, 2026 · 10:30:00 AM ET'))
  it('handles a UTC date rollover and invalid timestamp safely', () => { expect(formatEasternTime('2026-08-14T00:30:00Z')).toBe('Aug 13, 2026 · 8:30:00 PM ET'); expect(formatEasternTime()).toBe('N/A'); expect(formatEasternTime('not-a-time')).toBe('N/A') })
})