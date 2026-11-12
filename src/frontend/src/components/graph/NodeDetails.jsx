import { useEffect, useRef } from 'react'
import { nodeId } from './graphStyle.js'

export default function NodeDetails({ graph, selectedId, onSelect, onExpand, expanding }) {
  const heading = useRef(null)
  useEffect(() => { heading.current?.focus() }, [selectedId])
  const node = graph.nodes.find((candidate) => candidate.id === selectedId)
  const connected = node && graph.links.filter((link) => [nodeId(link.source), nodeId(link.target)].includes(node.id))
  const detail = node?.detail ?? {}
  return (
    <aside className="graph-details" aria-label="Graph node details">
      {node ? <>
        <button type="button" className="secondary" onClick={() => onSelect(null)}>All nodes</button>
        <p className="graph-node-type">{node.type}{detail.kind ? ` · ${detail.kind}` : ''}</p>
        <h3 ref={heading} tabIndex={-1}>{node.label}</h3>
        {node.type === 'Claim' && <p>Signal: {node.signal ?? 'Not scored'}</p>}
        {detail.quote && <blockquote>{detail.quote}</blockquote>}
        <dl>
          {['speaker', 'podcast', 'episode', 'published_at'].filter((key) => detail[key]).map((key) => (
            <div key={key}><dt>{key === 'published_at' ? 'Published' : key}</dt><dd>{detail[key]}</dd></div>
          ))}
        </dl>
        {/^https?:\/\//i.test(detail.source_url ?? '') && <a href={detail.source_url} target="_blank" rel="noreferrer">Open source ↗</a>}
        <button type="button" onClick={() => onExpand(node.id)} disabled={expanding}>
          {expanding ? 'Loading connections…' : 'Show connections'}
        </button>
        <h4>Connections</h4>
        <ul>{connected.map((link) => {
          const otherId = nodeId(link.source) === node.id ? nodeId(link.target) : nodeId(link.source)
          const other = graph.nodes.find((candidate) => candidate.id === otherId)
          return <li key={`${link.type}|${otherId}`}>
            <button type="button" className="graph-node-button" onClick={() => onSelect(otherId)}>
              <span>{link.type.replaceAll('_', ' ')}{link.strength != null ? ` · ${link.strength}` : ''} · {other.type}</span>
              {other.label}
            </button>
          </li>
        })}</ul>
      </> : <>
        <h3 ref={heading} tabIndex={-1}>Explore the evidence</h3>
        <p>Select a node to read its details. Pan, zoom, or drag to explore.</p>
        <h4>Nodes</h4>
        <ul>{graph.nodes.map((candidate) => <li key={candidate.id}>
          <button type="button" className="graph-node-button" onClick={() => onSelect(candidate.id)}>
            <span>{candidate.type}</span>{candidate.label}
          </button>
        </li>)}</ul>
      </>}
    </aside>
  )
}
