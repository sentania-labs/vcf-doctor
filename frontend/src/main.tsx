import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

const colorScheme = window.matchMedia('(prefers-color-scheme: dark)')
function applyTheme() { document.documentElement.dataset.theme = colorScheme.matches ? 'dark' : 'light' }
applyTheme()
colorScheme.addEventListener('change', applyTheme)

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
