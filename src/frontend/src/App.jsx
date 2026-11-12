import { useCallback, useEffect, useRef, useState } from 'react'
import { post } from './api.js'
import DbCabinet from './components/DbCabinet.jsx'
import PipelineStatus from './components/PipelineStatus.jsx'
import GraphCard from './components/graph/GraphCard.jsx'

const SUGGESTED_PROMPTS = [
  {
    label: 'Save a thesis',
    text: 'New thesis - Microsoft can capture enterprise AI spending through both Azure and its applications rather than risk disintermediation from AI.',
  },
  {
    label: 'Get email alerts',
    text: 'Regarding my thesis on Microsoft and its enterprise AI spending: email me at holtkam2@gmail.com if you see a high signal claim which refutes my theory',
  },
  {
    label: 'Follow a podcast',
    text: 'Follow the a16z podcast for new episodes',
  },
  {
    label: 'Conduct research',
    text: `What are the top 3 most high signal claims that have been made against my thesis "Nvidia's data center revenue will keep growing more than 40% a year through 2027"?`,
  },
  {
    label: 'Connect my theses',
    text: 'How are my Nvidia, AWS, and optical-interconnect theses connected? Limit each stage to at most 3 claims. '
      + 'Investigate in stages: start with the strongest evidence supporting and challenging my Nvidia growth thesis. '
      + 'Use the companies and technologies in those claims to find related evidence from other podcast episodes. '
      + 'Then trace how that evidence supports or challenges my AWS-capex and optical-interconnect theses. '
      + 'Show the graph connections as you go, then summarize the shared assumptions, disagreements, and strongest '
      + 'source quotes with speakers, episodes, and signal scores. Distinguish sharing an entity from actually '
      + 'supporting a thesis, and repeated claims within one episode from independent sources.',
  },
]

export default function App() {
  const [sessionId, setSessionId] = useState(null)
  const [connected, setConnected] = useState(false)
  const [busy, setBusy] = useState(false)
  const [draft, setDraft] = useState('')
  const [messages, setMessages] = useState([])
  const [error, setError] = useState('')
  const [ingestionJobId, setIngestionJobId] = useState(null)
  const [graphRequest, setGraphRequest] = useState(null)
  const openGraph = (graph, selectedId) => setGraphRequest({ graph, selectedId, key: crypto.randomUUID() })
  // Stable, so PipelineStatus's hide timer is not reset by chat re-renders.
  const clearIngestion = useCallback(() => setIngestionJobId(null), [])
  const sessionRequest = useRef(null)
  const bottom = useRef(null)
  const messageInput = useRef(null)
  const isEmpty = messages.length === 0

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
      stream.addEventListener('graph', (event) => {
        const { call_id, ...graph } = JSON.parse(event.data)
        setMessages((previous) => previous.some((message) => message.id === call_id)
          ? previous : [...previous, { id: call_id, role: 'graph', graph }])
      })
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
      <DbCabinet graphRequest={graphRequest} onUploaded={(job) => setIngestionJobId(job.job_id)} />
      {ingestionJobId && <PipelineStatus key={ingestionJobId} jobId={ingestionJobId} onDismiss={clearIngestion} />}
      <header>
        <h1>Podcast Analyst</h1>
        <p role="status">{busy ? 'Codex is replying…' : connected ? 'Ready' : 'Connecting to Codex…'}</p>
      </header>
      <section className={`chat-content${isEmpty ? ' is-empty' : ''}`} aria-label="Research chat">
        {isEmpty ? (
          <div className="welcome">
            <h2>What would you like to investigate?</h2>
            <p>Track investment theses, find podcast evidence, and follow new episodes.</p>
          </div>
        ) : (
          <ol className="messages" aria-label="Conversation">
            {messages.map((message) => (
              <li key={message.id} className={`message ${message.role}`}>
                {message.role === 'graph' ? <GraphCard graph={message.graph} onOpen={openGraph} /> : <>
                  <span className="speaker">{message.role === 'user' ? 'You' : 'Codex'}</span>
                  <p>{message.text}</p>
                </>}
              </li>
            ))}
            <li ref={bottom} aria-hidden="true" />
          </ol>
        )}
        {error && <p className="error" role="alert">{error}</p>}
        <form className="composer" onSubmit={sendMessage}>
          <label className="visually-hidden" htmlFor="message">Message</label>
          <textarea ref={messageInput} id="message" placeholder="Message Codex…" value={draft} rows={2}
            maxLength={20000} onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault()
                event.currentTarget.form.requestSubmit()
              }
            }} />
          <button type="submit" disabled={!connected || busy || !draft.trim()}>Send</button>
        </form>
        {isEmpty && (
          <div className="suggested-prompts" role="group" aria-label="Suggested prompts">
            {SUGGESTED_PROMPTS.map((prompt) => (
              <button key={prompt.label} type="button" className="suggested-prompt" title={prompt.text}
                onClick={() => {
                  setDraft(prompt.text)
                  messageInput.current?.focus()
                }}>
                <strong>{prompt.label}</strong>
                <span>{prompt.text}</span>
              </button>
            ))}
          </div>
        )}
      </section>
    </main>
  )
}
