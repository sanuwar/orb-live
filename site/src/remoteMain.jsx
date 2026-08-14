import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import RemoteApp from './remote/RemoteApp'
import './styles/tokens.css'
import './styles/global.css'
import './styles/components.css'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <RemoteApp />
  </StrictMode>,
)
