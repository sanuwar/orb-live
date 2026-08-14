import { subpageParents } from '../data/subpageRoutes'

export default function SubpageContext({ route }) {
  const context = subpageParents[route]
  if (!context) return null
  return <nav className="subpage-context" aria-label="Page hierarchy"><a href={`#/${context[1]}`}>Back to {context[0]}</a><span aria-hidden="true">/</span><strong>{context[2]}</strong></nav>
}