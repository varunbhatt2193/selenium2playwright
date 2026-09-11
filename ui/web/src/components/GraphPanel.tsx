import { Loader2 } from 'lucide-react'

/**
 * The agent's state machine, lit up as it runs.
 *
 * Not a drawing of the pipeline — the nodes here are the nodes the graph
 * actually reports. Every `updates` event from LangGraph names the node that
 * just finished, the page already receives them one by one, and this panel is
 * the same list read as a shape instead of as a list.
 *
 * The one thing a trail cannot show is the loop. `critic` sending a draft back
 * to `convert` is the whole argument for building this as a graph rather than a
 * prompt, and in a flat list it looks like nothing more than a repeated line.
 * Here the edge is drawn, and it counts.
 */

type Props = {
  /** Node names in the order the run reported them, `intake` first. */
  reported: string[]
  running: boolean
}

type Node = { id: string; label: string; hint: string; y: number }

// Laid out in the order the graph runs them. `refuse` is the one branch that
// leaves early — it sits off to the side and only appears if intake took it.
const NODES: Node[] = [
  { id: 'intake', label: 'intake', hint: 'classify or refuse', y: 8 },
  { id: 'recall', label: 'recall', hint: 'remembered conventions', y: 84 },
  { id: 'risk_review', label: 'risk_review', hint: 'pause if ambiguous', y: 160 },
  { id: 'convert', label: 'convert', hint: 'write Playwright', y: 236 },
  { id: 'validate', label: 'validate', hint: 'tsc · eslint · residue · parity', y: 312 },
  { id: 'critic', label: 'critic', hint: 'review the draft', y: 388 },
  { id: 'assemble', label: 'assemble', hint: 'report the outcome', y: 464 },
]

const BOX_X = 96
const BOX_W = 150
const BOX_H = 46

export default function GraphPanel({ reported, running }: Props) {
  const active = running ? reported[reported.length - 1] : ''
  const visited = new Set(reported)
  // Each visit to `convert` after the first is a repair the critic asked for.
  const laps = reported.filter((node) => node === 'convert').length
  const refused = visited.has('refuse')

  const state = (id: string): 'active' | 'done' | 'idle' => {
    if (id === active) return 'active'
    return visited.has(id) ? 'done' : 'idle'
  }

  return (
    <div className="graph-panel">
      <div className="graph-head">
        <strong>The graph, as it runs</strong>
        {laps > 1 && (
          <span className="graph-laps">
            repair lap {laps} of 3
          </span>
        )}
      </div>
      <svg
        className="graph-svg"
        viewBox={`0 0 342 ${NODES[NODES.length - 1].y + BOX_H + 12}`}
        role="img"
        aria-label={
          running
            ? `Conversion graph, currently at ${active || 'start'}`
            : 'Conversion graph: intake, recall, risk review, convert, validate, critic, assemble, with a repair loop from critic back to convert'
        }
      >
        <defs>
          <marker id="gp-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
            <path d="M0,0 L10,5 L0,10 z" fill="currentColor" />
          </marker>
        </defs>

        {/* straight edges, node to node */}
        {NODES.slice(0, -1).map((node, index) => {
          const next = NODES[index + 1]
          const lit = visited.has(node.id) && visited.has(next.id)
          return (
            <line
              key={`edge-${node.id}`}
              className={`graph-edge ${lit ? 'lit' : ''}`}
              x1={BOX_X + BOX_W / 2}
              y1={node.y + BOX_H}
              x2={BOX_X + BOX_W / 2}
              y2={next.y - 2}
              markerEnd="url(#gp-arrow)"
            />
          )
        })}

        {/* the repair loop: critic back up to convert */}
        <path
          className={`graph-edge loop ${laps > 1 ? 'lit' : ''}`}
          d={`M ${BOX_X} ${388 + BOX_H / 2}
              C 40 ${388 + BOX_H / 2}, 40 ${236 + BOX_H / 2}, ${BOX_X - 2} ${236 + BOX_H / 2}`}
          fill="none"
          markerEnd="url(#gp-arrow)"
        />
        <text className={`graph-loop-label ${laps > 1 ? 'lit' : ''}`} x="34" y="316" textAnchor="middle">
          repair
        </text>

        {/* the early exit */}
        <line
          className={`graph-edge ${refused ? 'lit' : 'faint'}`}
          x1={BOX_X + BOX_W}
          y1={8 + BOX_H / 2}
          x2={300}
          y2={8 + BOX_H / 2}
          markerEnd="url(#gp-arrow)"
        />
        <text className={`graph-hint ${refused ? 'lit' : 'faint'}`} x="303" y={8 + BOX_H / 2 + 4}>
          refuse
        </text>

        {NODES.map((node) => {
          const how = state(node.id)
          return (
            <g key={node.id} className={`graph-node ${how}`}>
              <rect x={BOX_X} y={node.y} width={BOX_W} height={BOX_H} rx="10" />
              <text className="graph-node-label" x={BOX_X + 12} y={node.y + 20}>
                {node.label}
              </text>
              <text className="graph-node-hint" x={BOX_X + 12} y={node.y + 35}>
                {node.hint}
              </text>
            </g>
          )
        })}
      </svg>
      <p className="graph-foot">
        {running ? (
          <>
            <Loader2 size={12} className="spin" /> {active || 'starting'}
          </>
        ) : reported.length ? (
          `${reported.length} node${reported.length === 1 ? '' : 's'} ran${laps > 1 ? `, ${laps - 1} repair lap${laps === 2 ? '' : 's'}` : ''}`
        ) : (
          'Every node below is one the graph reports as it finishes it.'
        )}
      </p>
    </div>
  )
}
