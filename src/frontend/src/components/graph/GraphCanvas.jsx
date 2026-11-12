import { useRef } from 'react'
import ForceGraph2D from 'react-force-graph-2d'
import { canvasColor, EDGE_STYLES, NODE_STYLES, nodeId, nodeRadius, textTooltip } from './graphStyle.js'

export default function GraphCanvas({ graph, width, height, selectedId, onSelect }) {
  const canvas = useRef(null)
  const fitted = useRef(false)
  const touchesSelection = (link) => !selectedId || [nodeId(link.source), nodeId(link.target)].includes(selectedId)
  // With a selection, everything but the selected node and its neighbors fades back.
  const near = selectedId && new Set([selectedId, ...graph.links.filter(touchesSelection)
    .flatMap((link) => [nodeId(link.source), nodeId(link.target)])])
  return (
    <ForceGraph2D ref={canvas} graphData={graph} width={width} height={height}
      backgroundColor="rgba(0,0,0,0)" cooldownTicks={100} nodeLabel={textTooltip} nodeRelSize={1}
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
        context.globalAlpha = near && !near.has(node.id) ? 0.25 : 1
        if (style.halo) {
          context.beginPath()
          context.arc(node.x, node.y, radius + 6, 0, 2 * Math.PI)
          context.fillStyle = canvasColor(style.halo, 0.16)
          context.fill()
        }
        context.beginPath()
        context.arc(node.x, node.y, radius, 0, 2 * Math.PI)
        context.fillStyle = canvasColor(style.fill)
        context.fill()
        if (style.stroke) {
          context.strokeStyle = canvasColor(style.stroke)
          context.lineWidth = 1.4 / scale
          context.stroke()
        }
        if (node.id === selectedId) {
          context.beginPath()
          context.arc(node.x, node.y, radius + 3.5, 0, 2 * Math.PI)
          context.strokeStyle = canvasColor('--color-accent-200')
          context.lineWidth = 1.2 / scale
          context.stroke()
        }
        if (['Thesis', 'Episode'].includes(node.type) || node.id === selectedId || scale > 2) {
          const label = node.label.length > 32 ? `${node.label.slice(0, 32)}…` : node.label
          const y = node.y + radius + 12 / scale
          context.font = `${11 / scale}px Inter, system-ui, sans-serif`
          context.textAlign = 'center'
          context.lineJoin = 'round'
          context.lineWidth = 3 / scale
          context.strokeStyle = canvasColor('--color-bg')
          context.strokeText(label, node.x, y)
          context.fillStyle = canvasColor(node.type === 'Thesis' ? '--color-neutral-200' : '--color-neutral-400')
          context.fillText(label, node.x, y)
        }
        context.globalAlpha = 1
      }}
      nodeVal={(node) => nodeRadius(node) ** 2}
      linkColor={(link) => canvasColor(EDGE_STYLES[link.type].color, touchesSelection(link) ? 1 : 0.12)}
      linkWidth={(link) => link.strength == null ? 0.6 : 0.8 + link.strength / 60}
      linkLineDash={(link) => EDGE_STYLES[link.type].dashed ? [4, 3] : null}
      linkDirectionalArrowLength={(link) => link.strength == null ? 0 : 5}
      linkDirectionalArrowRelPos={1} />
  )
}
