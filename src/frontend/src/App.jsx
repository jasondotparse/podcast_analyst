import { useEffect, useState } from 'react'

export default function App() {
  const [health, setHealth] = useState('Checking...')
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    setHealth('Checking...')
    fetch('/api/health', { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error('Health check failed')
        return response.json()
      })
      .then((data) => setHealth(data.status))
      .catch((error) => {
        if (error.name !== 'AbortError') setHealth('Unavailable')
      })
    return () => controller.abort()
  }, [attempt])

  return (
    <main>
      <h1>Hello world!</h1>
      <p role="status">Backend: {health}</p>
      <button onClick={() => setAttempt((value) => value + 1)}>Check backend</button>
    </main>
  )
}
