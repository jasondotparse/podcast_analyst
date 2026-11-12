import { useRef } from 'react'
import ForceGraph2D from 'react-force-graph-2d'
import { EDGE_STYLES, NODE_STYLES, nodeId, nodeRadius, textTooltip } from './graphStyle.js'

export default function GraphCanvas({ graph, width, height, selectedId, onSelect }) {
  const canvas = useRef(null)
  const fitted = useRef(false)
  const touchesSelection = (link) => !selectedId || [nodeId(link.source), nodeId(link.target)].includes(selectedId)
  return (
    <ForceGraph2D ref={canvas} graphData={graph} width={width} height={height}
      backgroundColor="#ffffff" cooldownTicks={100} nodeLabel={textTooltip} nodeRelSize={1}
      onNodeClick={(node) => onSelect?.(node.id)} onBackgroundClick={() => onSelect?.(null)}
      onEngineStop={() => {
        if (!fitted.current) {
          fitted.current = true
          canvas.current?.zoomToFit(300, 45)
        }
      }}
      nodeCanvasObject={(node, context, scale) => {
        const style = NODE_STYLES[node.type]
        const radius = nodeRadius(node)
        context.beginPath()
        context.arc(node.x, node.y, radius, 0, 2 * Math.PI)
        context.fillStyle = style.color
        context.fill()
        if (node.id === selectedId) {
          context.beginPath()
          context.arc(node.x, node.y, radius + 3 / scale, 0, 2 * Math.PI)
          context.strokeStyle = '#17212e'
          context.lineWidth = 1.5 / scale
          context.stroke()
        }
        if (['Thesis', 'Episode'].includes(node.type) || scale > 2) {
          const label = node.label.length > 32 ? `${node.label.slice(0, 32)}…` : node.label
          context.font = `${12 / scale}px system-ui`
          context.textAlign = 'center'
          context.fillStyle = '#17212e'
          context.fillText(label, node.x, node.y + radius + 14 / scale)
        }
      }}
      nodeVal={(node) => nodeRadius(node) ** 2}
      linkColor={(link) => EDGE_STYLES[link.type].color + (touchesSelection(link) ? '' : '26')}
      linkWidth={(link) => link.strength == null ? 0.5 : 1 + link.strength / 25}
      linkLineDash={(link) => link.type === 'CHALLENGES' ? [5, 3] : null}
      linkDirectionalArrowLength={(link) => link.strength == null ? 0 : 5}
      linkDirectionalArrowRelPos={1} />
  )
}
