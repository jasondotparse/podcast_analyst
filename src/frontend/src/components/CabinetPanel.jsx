import { useEffect, useRef } from 'react'
import CabinetList from './CabinetList.jsx'
import GraphPanel from './graph/GraphPanel.jsx'

// The open cabinet drawer: the chosen view, with Close at the top. Switching views
// remounts only the list (keyed by view), so the drawer doesn't replay its slide-in.
export default function CabinetPanel({ view, onClose, graphRequest }) {
  const closeButton = useRef(null)
  useEffect(() => { closeButton.current?.focus() }, [view.id, graphRequest])
  return (
    <section id="cabinet-panel" className={`cabinet-panel${view.id === 'graph' ? ' graph-cabinet' : ''}`} aria-labelledby="cabinet-title">
      <div className="cabinet-header">
        <h2 id="cabinet-title">{view.title ?? view.label}</h2>
        <button ref={closeButton} type="button" className="secondary" onClick={onClose}>Close</button>
      </div>
      <div className="cabinet-body">
        {view.id === 'graph'
          ? <GraphPanel key={graphRequest?.key ?? 'overview'} initialGraph={graphRequest?.graph} initialSelectedId={graphRequest?.selectedId} />
          : view.load
          ? <CabinetList key={view.id} view={view} />
          : <p className="cabinet-note">{view.placeholder}</p>}
      </div>
    </section>
  )
}
