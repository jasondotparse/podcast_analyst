import { CaretRightIcon, CheckCircleIcon, CircleNotchIcon, ProhibitIcon, WarningCircleIcon, XCircleIcon } from '@phosphor-icons/react'

const STATUS = {
  running: { label: 'Running…', icon: CircleNotchIcon },
  succeeded: { label: 'Succeeded', icon: CheckCircleIcon },
  failed: { label: 'Failed', icon: XCircleIcon },
  declined: { label: 'Declined', icon: ProhibitIcon },
  interrupted: { label: 'Interrupted', icon: WarningCircleIcon },
  ended: { label: 'Ended without completion', icon: WarningCircleIcon },
}

const clip = (value, max = 90) => {
  const text = String(value ?? '').replace(/\s+/g, ' ').trim()
  return text.length > max ? `${text.slice(0, max - 1)}…` : text
}

function actionText(action) {
  if (!action) return ''
  return action.query || action.queries?.join(', ') || action.url || action.type || ''
}

// A deterministic one-line summary of the most relevant argument; the expanded view shows everything.
function preview(tool) {
  const args = tool.arguments && typeof tool.arguments === 'object' ? tool.arguments : {}
  switch (tool.kind === 'dynamicToolCall' ? tool.name?.split('.').pop() : tool.name) {
    case 'vector_search': return clip(args.query)
    case 'postgres_database_query': return clip(args.sql)
    case 'graph_query': return clip(args.cypher)
    case 'register_rss_to_watcher': return clip(args.feed_url, 200)
    case 'add_thesis': return clip(args.description)
    case 'add_subscription': return clip(args.email || `${args.thesis_ids?.length ?? 0} thesis IDs`)
    case 'web_search': return clip(args.query || actionText(args.action))
    case 'command_execution': return clip(args.command)
  }
  if (tool.kind === 'mcpToolCall') {
    return clip(Object.values(args).find((value) => typeof value === 'string') || 'Tool invocation')
  }
  return clip(JSON.stringify(tool.arguments ?? null))
}

const display = (value) => typeof value === 'string' ? value : JSON.stringify(value, null, 2)

const time = (ms) => ms == null ? '—' : new Date(ms).toLocaleTimeString([], {
  hour: 'numeric', minute: '2-digit', second: '2-digit', fractionalSecondDigits: 3,
})

const duration = (ms) => ms == null ? '' : ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`

// Native <details> keeps its own open state, so in-place updates never collapse an expanded row.
export default function ToolActivity({ tool }) {
  const status = STATUS[tool.status] ?? STATUS.running
  const approx = tool.approximateTimes ? ' (approx. receipt time)' : ''
  return (
    <details className={`tool-activity ${tool.status}`}>
      <summary>
        <status.icon className="tool-status-icon" size={14} aria-label={status.label} />
        <span className="tool-label">Using tools</span>
        <code className="tool-name">{tool.name}</code>
        <span className="tool-preview">{preview(tool)}</span>
        <span className="tool-timing">{tool.status === 'running' ? status.label : duration(tool.durationMs)}</span>
        <CaretRightIcon className="tool-caret" size={12} aria-hidden="true" />
      </summary>
      <dl className="tool-details">
        <dt>Tool</dt><dd><code>{tool.name}</code> <span className="tool-kind">{tool.kind}</span></dd>
        <dt>Status</dt><dd>{status.label}</dd>
        <dt>Started</dt><dd>{time(tool.startedAtMs)}{tool.startedAtMs != null && approx}</dd>
        <dt>Completed</dt><dd>{time(tool.completedAtMs)}{tool.completedAtMs != null && approx}</dd>
        {tool.durationMs != null && <><dt>Duration</dt><dd>{duration(tool.durationMs)}</dd></>}
      </dl>
      <h4>Arguments</h4>
      <pre>{display(tool.arguments ?? null)}</pre>
      <h4>Response{tool.truncated && <span className="tool-truncated"> · truncated to 40,000 characters for display</span>}</h4>
      {tool.result != null ? <pre>{display(tool.result)}</pre>
        : <p className="tool-empty">{tool.status === 'running' ? 'Waiting for the tool to finish…' : 'No result details available'}</p>}
    </details>
  )
}
