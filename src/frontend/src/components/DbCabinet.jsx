import { useCallback, useEffect, useRef, useState } from 'react'
import CabinetPanel from './CabinetPanel.jsx'
import { CABINET_VIEWS } from './cabinetViews.jsx'
import './DbCabinet.css'

// A left-hand rail of buttons, minimized until one is clicked; each opens a drawer
// listing part of what the database holds.
export default function DbCabinet({ graphRequest }) {
  const [openId, setOpenId] = useState(null)
  const [initialGraph, setInitialGraph] = useState(null)
  const returnFocus = useRef(null)
  const graphButton = useRef(null)
  const view = CABINET_VIEWS.find((candidate) => candidate.id === openId)
  const close = useCallback(() => {
    setOpenId(null)
    const trigger = returnFocus.current
    ;(trigger?.isConnected && trigger !== document.body ? trigger : graphButton.current)?.focus()
  }, [])

  useEffect(() => {
    if (!graphRequest) return
    returnFocus.current = document.activeElement
    setInitialGraph(graphRequest)
    setOpenId('graph')
  }, [graphRequest])

  useEffect(() => {
    if (!openId) return
    const escape = (event) => { if (event.key === 'Escape') close() }
    document.addEventListener('keydown', escape)
    return () => document.removeEventListener('keydown', escape)
  }, [openId, close])

  return (
    <aside className="cabinet" aria-label="Database cabinet">
      <nav className="cabinet-rail">
        <p className="cabinet-heading">Database</p>
        {CABINET_VIEWS.map((candidate) => (
          <button key={candidate.id} type="button" className="cabinet-tab"
            ref={candidate.id === 'graph' ? graphButton : null}
            aria-pressed={candidate.id === openId} aria-controls="cabinet-panel"
            onClick={(event) => {
              returnFocus.current = event.currentTarget
              if (openId === candidate.id) return close()
              setInitialGraph(null)
              setOpenId(candidate.id)
            }}>
            {candidate.label}
          </button>
        ))}
      </nav>
      {view && <CabinetPanel view={view} onClose={close} graphRequest={initialGraph} />}
    </aside>
  )
}
