import { cssVar, EDGE_STYLES, NODE_STYLES } from './graphStyle.js'

export default function GraphLegend() {
  return <div className="graph-legend" aria-label="Graph legend">
    {Object.entries(NODE_STYLES).map(([type, style]) => (
      <span key={type}><i className="graph-dot" style={{
        background: cssVar(style.fill),
        boxShadow: style.stroke ? `inset 0 0 0 1.4px ${cssVar(style.stroke)}` : undefined,
      }} />{type}</span>
    ))}
    {Object.entries(EDGE_STYLES).map(([type, style]) => (
      <span key={type}><i className="graph-line" style={{ borderColor: cssVar(style.color), borderTopStyle: style.dashed ? 'dashed' : 'solid' }} />{style.label}</span>
    ))}
  </div>
}
