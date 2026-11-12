import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { getGraph, getNeighbors } from '../../api.js'
import { copyGraph, linkKey, nodeId } from './graphStyle.js'
import GraphLegend from './GraphLegend.jsx'
import NodeDetails from './NodeDetails.jsx'
import useGraphSize from './useGraphSize.js'
import './GraphPanel.css'

const GraphCanvas = lazy(() => import('./GraphCanvas.jsx'))
const EMPTY_GRAPH = { nodes: [], links: [], truncated: false }

export default function GraphPanel({ initialGraph, initialSelectedId }) {
  const [graph, setGraph] = useState(() => copyGraph(initialGraph ?? EMPTY_GRAPH))
  const [theses, setTheses] = useState([])
  const [thesisId, setThesisId] = useState(initialGraph ? 'chat' : '')
  const [selectedId, setSelectedId] = useState(initialSelectedId ?? null)
  const [loading, setLoading] = useState(!initialGraph)
  const [expanding, setExpanding] = useState(false)
  const [error, setError] = useState('')
  const [layout, setLayout] = useState(0)
  const [container, size] = useGraphSize()
  const requestId = useRef(0)

  useEffect(() => {
    let active = true
    getGraph().then((overview) => {
      if (!active) return
      setTheses(overview.nodes.filter((node) => node.type === 'Thesis'))
      if (!initialGraph) setGraph(copyGraph(overview))
    }).catch((failure) => { if (active) setError(failure.message) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false; requestId.current++ }
  }, [initialGraph])

  async function loadView(id) {
    const current = ++requestId.current
    setThesisId(id)
    setLoading(true)
    setError('')
    setExpanding(false)
    try {
      const next = id === 'chat' ? initialGraph : await getGraph(id)
      if (current !== requestId.current) return
      setGraph(copyGraph(next))
      setSelectedId(id === 'chat' ? initialSelectedId ?? null : null)
      setLayout((value) => value + 1)
    } catch (failure) {
      if (current === requestId.current) setError(failure.message)
    } finally {
      if (current === requestId.current) setLoading(false)
    }
  }

  async function expand(id) {
    const current = requestId.current
    setExpanding(true)
    setError('')
    try {
      const next = await getNeighbors(id)
      if (current !== requestId.current) return
      setGraph((previous) => {
        const nodes = new Map(previous.nodes.map((node) => [node.id, node]))
        let truncated = previous.truncated || next.truncated
        for (const node of next.nodes) {
          if (nodes.has(node.id)) continue
          if (nodes.size >= 300) { truncated = true; continue }
          nodes.set(node.id, node)
        }
        const links = new Map(previous.links.map((link) => [linkKey(link), link]))
        for (const link of next.links) {
          if (nodes.has(nodeId(link.source)) && nodes.has(nodeId(link.target)) && !links.has(linkKey(link))) {
            links.set(linkKey(link), link)
          }
        }
        return { nodes: [...nodes.values()], links: [...links.values()], truncated }
      })
    } catch (failure) {
      if (current === requestId.current) setError(failure.message)
    } finally {
      if (current === requestId.current) setExpanding(false)
    }
  }

  return (
    <div className="graph-panel">
      <div className="graph-toolbar">
        <label>Thesis <select value={thesisId} onChange={(event) => loadView(event.target.value)} disabled={loading}>
          {initialGraph && <option value="chat">Chat subgraph</option>}
          <option value="">All theses</option>
          {theses.map((node) => <option key={node.id} value={node.id}>{node.label}</option>)}
        </select></label>
        <button type="button" className="secondary" onClick={() => loadView(thesisId)} disabled={loading}>Reset</button>
        <span role="status">{loading ? 'Loading graph…' : `${graph.nodes.length} nodes · ${graph.links.length} connections`}</span>
      </div>
      {error && <p className="error" role="alert">{error} <button type="button" onClick={() => loadView(thesisId)}>Try again</button></p>}
      {graph.truncated && <p className="graph-notice">View limited to keep the graph readable. Select a node to explore its connections, or reset.</p>}
      <div className="graph-workspace">
        <div className="graph-stage" ref={container}>
          {graph.nodes.length > 0 && size.width > 0 && size.height > 0 && <Suspense fallback={<p>Loading canvas…</p>}>
            <GraphCanvas key={layout} graph={graph} {...size} selectedId={selectedId} onSelect={setSelectedId} />
          </Suspense>}
          {!loading && !error && !graph.nodes.length && <p className="graph-empty">Add a thesis to view the knowledge graph.</p>}
          <GraphLegend />
        </div>
        <NodeDetails graph={graph} selectedId={selectedId} onSelect={setSelectedId} onExpand={expand} expanding={expanding} />
      </div>
    </div>
  )
}
