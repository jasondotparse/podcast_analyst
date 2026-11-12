// Colors are Nocturne token names: the legend paints them with var(), the canvas via canvasColor().
export const NODE_STYLES = {
  Thesis: { fill: '--color-accent-300', halo: '--color-accent', radius: 9 },
  Episode: { fill: '--color-bg', stroke: '--color-neutral-300', radius: 6 },
  Entity: { fill: '--color-accent-600', radius: 4.5 },
  Claim: { fill: '--color-neutral-400', radius: 3 },
}
export const EDGE_STYLES = {
  CHALLENGES: { color: '--color-error', label: 'Challenges', dashed: true },
  SUPPORTS: { color: '--color-accent-400', label: 'Supports' },
  ABOUT: { color: '--color-neutral-700', label: 'About' },
  IN_EPISODE: { color: '--color-neutral-800', label: 'In episode' },
}
export const nodeId = (node) => node?.id ?? node
export const nodeRadius = (node) => NODE_STYLES[node.type].radius + (node.type === 'Claim' ? (node.signal ?? 0) / 25 : 0)
export const linkKey = (link) => `${nodeId(link.source)}|${link.type}|${nodeId(link.target)}`
export const cssVar = (token) => `var(${token})`

// Canvas can't read var(), so resolve each token once (to sRGB, so it can take an alpha).
const resolved = new Map()
let probe
export function canvasColor(token, alpha = 1) {
  if (!resolved.has(token)) {
    probe ??= document.createElement('canvas').getContext('2d', { willReadFrequently: true })
    probe.clearRect(0, 0, 1, 1)
    probe.fillStyle = '#000'
    probe.fillStyle = getComputedStyle(document.documentElement).getPropertyValue(token).trim()
    probe.fillRect(0, 0, 1, 1)
    resolved.set(token, probe.getImageData(0, 0, 1, 1).data.slice(0, 3).join(', '))
  }
  return `rgba(${resolved.get(token)}, ${alpha})`
}

// Each canvas owns its layout objects; force-graph mutates nodes and link endpoints.
export function copyGraph(graph) {
  return {
    ...graph,
    nodes: graph.nodes.map((node) => ({ ...node })),
    links: graph.links.map((link) => ({ ...link, source: nodeId(link.source), target: nodeId(link.target) })),
  }
}

export function textTooltip(node) {
  const element = document.createElement('span')
  element.textContent = node.label
  return element
}
