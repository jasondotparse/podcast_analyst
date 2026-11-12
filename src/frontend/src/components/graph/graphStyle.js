export const NODE_STYLES = {
  Thesis: { color: '#17212e', radius: 9 },
  Claim: { color: '#898781', radius: 3 },
  Entity: { color: '#1baf7a', radius: 4 },
  Episode: { color: '#eda100', radius: 6 },
}
export const EDGE_STYLES = {
  CHALLENGES: { color: '#e34948', label: 'Challenges', dashed: true },
  SUPPORTS: { color: '#2a78d6', label: 'Supports' },
  ABOUT: { color: '#a5adb6', label: 'About' },
  IN_EPISODE: { color: '#d3d8df', label: 'In episode' },
}
export const nodeId = (node) => node?.id ?? node
export const nodeRadius = (node) => NODE_STYLES[node.type].radius + (node.type === 'Claim' ? (node.signal ?? 0) / 25 : 0)
export const linkKey = (link) => `${nodeId(link.source)}|${link.type}|${nodeId(link.target)}`

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
