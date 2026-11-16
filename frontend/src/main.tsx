import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
// Styles: tokens, shared components, then each page's own. The order is the cascade's.
import './styles/theme.css'
import './styles/components.css'
import './styles/setup.css'
import './styles/preview.css'
import './styles/publish.css'
import './styles/sheet.css'
import './styles/capacity.css'
import './styles/status.css'
import './styles/masterdata.css'
import './styles/responsibilities.css'
import './styles/users.css'
import './styles/notes.css'
import './styles/itemlists.css'
import './styles/forwarding.css'
import './styles/settings.css'
import './styles/workforce.css'
import './styles/personnel.css'
import './styles/transfers.css'
import './styles/home.css'
import './styles/capex.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
