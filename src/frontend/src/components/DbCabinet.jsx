import { useState } from 'react'
import CabinetPanel from './CabinetPanel.jsx'
import { CABINET_VIEWS } from './cabinetViews.jsx'
import './DbCabinet.css'

// A left-hand rail of buttons, minimized until one is clicked; each opens a drawer
// listing part of what the database holds.
export default function DbCabinet() {
  const [openId, setOpenId] = useState(null)
  const view = CABINET_VIEWS.find((candidate) => candidate.id === openId)

  return (
    <aside className="cabinet" aria-label="Database cabinet">
      <nav className="cabinet-rail">
        <p className="cabinet-heading">Database</p>
        {CABINET_VIEWS.map((candidate) => (
          <button key={candidate.id} type="button" className="cabinet-tab"
            aria-pressed={candidate.id === openId} aria-controls="cabinet-panel"
            onClick={() => setOpenId(candidate.id)}>
            {candidate.label}
          </button>
        ))}
      </nav>
      {view && <CabinetPanel view={view} onClose={() => setOpenId(null)} />}
    </aside>
  )
}
