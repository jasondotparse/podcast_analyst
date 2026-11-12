import { lazy, Suspense, useMemo } from 'react'
import { copyGraph } from './graphStyle.js'
import GraphLegend from './GraphLegend.jsx'
import useGraphSize from './useGraphSize.js'
import './GraphPanel.css'

const GraphCanvas = lazy(() => import('./GraphCanvas.jsx'))

export default function GraphCard({ graph, onOpen }) {
  const layoutGraph = useMemo(() => copyGraph(graph), [graph])
  const [container, size] = useGraphSize()
  return (
    <div className="graph-card">
      <div className="graph-card-stage" ref={container} aria-label="Graph query preview">
        {size.width > 0 && <Suspense fallback={<p>Loading canvas…</p>}>
          <GraphCanvas graph={layoutGraph} {...size} onSelect={(id) => { if (id) onOpen(graph, id) }} />
        </Suspense>}
        <GraphLegend />
      </div>
      <div className="graph-card-caption">
        <span>{graph.nodes.length} nodes · {graph.links.length} connections{graph.truncated ? ' · limited view' : ''}</span>
        <button type="button" className="secondary" onClick={() => onOpen(graph)}>Open in graph view</button>
      </div>
    </div>
  )
}
