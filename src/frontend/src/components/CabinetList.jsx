import useCabinetItems from '../hooks/useCabinetItems.js'

// One cabinet view's rows, fetched when it mounts.
export default function CabinetList({ view }) {
  const { items, error } = useCabinetItems(view.load)

  if (error) return <p className="error" role="alert">{error}</p>
  if (!items) return <p className="cabinet-note">Loading…</p>
  if (items.length === 0) return <p className="cabinet-note">{view.empty}</p>
  return (
    <ol className="cabinet-list">
      {items.map((item) => <li key={item.id}>{view.render(item)}</li>)}
    </ol>
  )
}
