import { useEffect, useRef, useState } from 'react'

async function post(path, body) {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body ?? {}),
  })
  const data = await response.json()
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Request failed.')
  return data
}

export default function App() {
  const [sessionId, setSessionId] = useState(null)
  const [connected, setConnected] = useState(false)
  const [busy, setBusy] = useState(false)
  const [draft, setDraft] = useState('')
  const [messages, setMessages] = useState([])
  const [error, setError] = useState('')
  const sessionRequest = useRef(null)
  const bottom = useRef(null)

  useEffect(() => {
    let active = true
    let stream
    // StrictMode re-runs effects in dev; reuse the initial request rather than
    // starting two Codex threads for one page.
    sessionRequest.current ??= post('/api/sessions')
    sessionRequest.current.then(({ session_id }) => {
      if (!active) return
      setSessionId(session_id)
      stream = new EventSource(`/api/sessions/${session_id}/events`)
      stream.addEventListener('ready', () => {
        setConnected(true)
        setError('')
      })
      const updateAssistant = (event, replace) => {
        const { item_id, text } = JSON.parse(event.data)
        setMessages((previous) => {
          const existing = previous.find((message) => message.id === item_id)
          if (!existing) return [...previous, { id: item_id, role: 'assistant', text }]
          return previous.map((message) => message.id === item_id
            ? { ...message, text: replace ? text : message.text + text }
            : message)
        })
      }
      stream.addEventListener('delta', (event) => updateAssistant(event, false))
      stream.addEventListener('message', (event) => updateAssistant(event, true))
      stream.addEventListener('completed', (event) => {
        const result = JSON.parse(event.data)
        setBusy(false)
        if (result.status !== 'completed') setError(result.error || `Turn ${result.status}.`)
      })
      stream.addEventListener('fatal', (event) => {
        setError(JSON.parse(event.data).message)
        setConnected(false)
        setBusy(false)
        stream.close()
      })
      stream.onerror = () => {
        setConnected(false)
        setError('Connection lost. Reconnecting; reload the page if the backend was restarted.')
      }
    }).catch((failure) => {
      if (active) setError(failure.message)
    })
    return () => {
      active = false
      stream?.close()
    }
  }, [])

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: 'end' })
  }, [messages])

  async function sendMessage(event) {
    event.preventDefault()
    const message = draft.trim()
    if (!message || !connected || busy) return
    setMessages((previous) => [...previous, { id: crypto.randomUUID(), role: 'user', text: message }])
    setDraft('')
    setBusy(true)
    setError('')
    try {
      await post(`/api/sessions/${sessionId}/turns`, { message })
      // Only the terminal SSE event completes a turn, not this HTTP response.
    } catch (failure) {
      setError(failure.message)
      setBusy(false)
    }
  }

  return (
    <main className="chat">
      <header>
        <h1>Podcast Analyst</h1>
        <p role="status">{busy ? 'Codex is replying…' : connected ? 'Ready' : 'Connecting to Codex…'}</p>
      </header>
      <ol className="messages" aria-label="Conversation">
        {messages.length === 0 && <li className="empty">Send a message to start a conversation.</li>}
        {messages.map((message) => (
          <li key={message.id} className={`message ${message.role}`}>
            <span className="speaker">{message.role === 'user' ? 'You' : 'Codex'}</span>
            <p>{message.text}</p>
          </li>
        ))}
        <li ref={bottom} aria-hidden="true" />
      </ol>
      {error && <p className="error" role="alert">{error}</p>}
      <form onSubmit={sendMessage}>
        <label className="visually-hidden" htmlFor="message">Message</label>
        <textarea id="message" placeholder="Message Codex…" value={draft} rows={2}
          maxLength={20000} onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault()
              event.currentTarget.form.requestSubmit()
            }
          }} />
        <button type="submit" disabled={!connected || busy || !draft.trim()}>Send</button>
      </form>
    </main>
  )
}
