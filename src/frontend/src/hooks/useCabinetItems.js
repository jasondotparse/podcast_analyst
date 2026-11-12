import { useEffect, useState } from 'react'

// Fetches one cabinet list when its view opens. Each view mounts its own list,
// so reopening or switching views always shows fresh rows.
export default function useCabinetItems(load) {
  const [items, setItems] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    load().then(
      (rows) => active && setItems(rows),
      (failure) => active && setError(failure.message),
    )
    return () => {
      active = false
    }
  }, [load])

  return { items, error }
}
