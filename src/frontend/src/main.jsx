import './nocturne.css'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.jsx'
import Onboarding from './components/Onboarding.jsx'
import './style.css'

// A browser's first visit goes to the onboarding primer, which renders without App, so it opens
// no chat session. If storage can't be read or written, count onboarding as seen: fail open to
// the app, never into a redirect loop.
const ONBOARDING_PATH = '/app/onboarding'
const SEEN_KEY = 'podcastAnalyst.onboardingSeen'

function onboardingSeen() {
  try { return localStorage.getItem(SEEN_KEY) !== null } catch { return true }
}

function markOnboardingSeen() {
  try { localStorage.setItem(SEEN_KEY, '1'); return true } catch { return false }
}

function render(page) {
  createRoot(document.getElementById('root')).render(<StrictMode>{page}</StrictMode>)
}

if (location.pathname === ONBOARDING_PATH) {
  markOnboardingSeen()
  render(<Onboarding />)
} else if (onboardingSeen() || !markOnboardingSeen()) {
  // Seen before, or storage can't remember it: show the app.
  render(<App />)
} else {
  location.replace(ONBOARDING_PATH)
}
