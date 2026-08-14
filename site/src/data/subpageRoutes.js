export const subpageParents = {
  journal: ['Performance', 'performance', 'Journal overview'],
  'journal-records': ['Performance', 'performance', 'Trades & events'],
  'journal-detail': ['Performance', 'performance', 'Journal detail'],
  'manual-record': ['Performance', 'performance', 'Add manual record'],
  'evidence-import': ['Performance', 'performance', 'Import sanitized evidence'],
  strategy: ['Strategy & System', 'strategy-system', 'Strategy'],
  'system-flow': ['Strategy & System', 'strategy-system', 'System flow'],
  safety: ['Strategy & System', 'strategy-system', 'Safety'],
  'architecture-glossary': ['Strategy & System', 'strategy-system', 'Architecture & glossary'],
}

export function primaryRoute(route) { return subpageParents[route]?.[1] ?? route }
