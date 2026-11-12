import CabinetList from './CabinetList.jsx'

// The open cabinet drawer: the chosen view, with Close at the top. Switching views
// remounts only the list (keyed by view), so the drawer doesn't replay its slide-in.
export default function CabinetPanel({ view, onClose }) {
  return (
    <section id="cabinet-panel" className="cabinet-panel" aria-labelledby="cabinet-title">
      <div className="cabinet-header">
        <h2 id="cabinet-title">{view.title ?? view.label}</h2>
        <button type="button" className="secondary" onClick={onClose}>Close</button>
      </div>
      <div className="cabinet-body">
        {view.load
          ? <CabinetList key={view.id} view={view} />
          : <p className="cabinet-note">{view.placeholder}</p>}
      </div>
    </section>
  )
}
