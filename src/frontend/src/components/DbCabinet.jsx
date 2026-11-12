import { useCallback, useEffect, useRef, useState } from 'react'
import { CompassIcon, SignOutIcon } from '@phosphor-icons/react'
import CabinetPanel from './CabinetPanel.jsx'
import UploadPodcastButton from './UploadPodcastButton.jsx'
import { CABINET_VIEWS } from './cabinetViews.jsx'
import './DbCabinet.css'

// A left-hand rail: Upload podcast at the top, Onboarding and Sign out at the bottom, and between them
// buttons that each open a drawer listing part of what the database holds.
export default function DbCabinet({ graphRequest, onUploaded }) {
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
        <div className="cabinet-brand" aria-hidden="true"><span className="cabinet-brand-mark" />Podcast Analyst</div>
        <UploadPodcastButton className="btn btn-primary cabinet-action" onUploaded={onUploaded} />
        <div className="cabinet-tabs">
          <div className="cabinet-label" aria-hidden="true">Database</div>
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
              <candidate.icon size={16} aria-hidden="true" />{candidate.label}
            </button>
          ))}
        </div>
        <div className="cabinet-footer">
          <a className="btn btn-secondary cabinet-action" href="/app/onboarding"><CompassIcon size={15} aria-hidden="true" />Onboarding</a>
          <form className="cabinet-signout" action="/api/logout" method="post">
            <button type="submit" className="btn btn-secondary cabinet-action"><SignOutIcon size={15} aria-hidden="true" />Sign out</button>
          </form>
        </div>
      </nav>
      {view && <CabinetPanel view={view} onClose={close} graphRequest={initialGraph} />}
    </aside>
  )
}
