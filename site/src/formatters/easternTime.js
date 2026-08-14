const formatter = new Intl.DateTimeFormat('en-US', {
  timeZone: 'America/New_York',
  year: 'numeric',
  month: 'short',
  day: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
  second: '2-digit',
  hour12: true,
})

export function formatEasternTime(timestamp) {
  if (typeof timestamp !== 'string' || !timestamp) return 'N/A'
  const instant = new Date(timestamp)
  if (Number.isNaN(instant.valueOf())) return 'N/A'
  const rendered = formatter.format(instant).replace(/, (?=\d{1,2}:\d{2}:\d{2} [AP]M$)/, ' ' + String.fromCharCode(0x00b7) + ' ')
  return `${rendered} ET`
}