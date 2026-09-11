import { Loader2 } from 'lucide-react'

/**
 * The suite graph, lit as it runs.
 *
 * A different graph from the one on the single-file page, and deliberately so:
 * this is the outer machine — `plan` works out what converts before what,
 * `next_wave` dispatches one wave, `convert_file` runs the whole conversion
 * graph once per file in that wave (in parallel), and `finish` compiles the
 * result as one project. The edge worth drawing is `convert_file` going back to
 * `next_wave`: that is "page objects first, then the specs that import them",
 * and it is the reason a suite is not just a loop over files.
 *
 * Every node here is one the run actually reports. The counts are the run's
 * own: which wave was dispatched, how many files have landed.
 */

type Props = {
  /** Last top-level node the run reported: plan | next_wave | convert_file | finish. */
  stage: string
  /** Which wave is in flight, 1-based; 0 before the first is dispatched. */
  wave: number
  /** How many waves the plan said there were. */
  waves: number
  landed: number
  expected: number
  running: boolean
  done: boolean
}

type Node = { id: string; label: string; hint: string; y: number }

const NODES: Node[] = [
  { id: 'plan', label: 'plan', hint: 'scan imports, order the waves', y: 8 },
  { id: 'next_wave', label: 'next_wave', hint: 'dispatch one wave', y: 96 },
  { id: 'convert_file', label: 'convert_file', hint: 'the conversion graph, per file', y: 184 },
  { id: 'finish', label: 'finish', hint: 'compile the tree as one project', y: 272 },
]

const ORDER = ['plan', 'next_wave', 'convert_file', 'finish']
const BOX_X = 96
const BOX_W = 170
const BOX_H = 52

export default function SuiteGraphPanel({
  stage,
  wave,
  waves,
  landed,
  expected,
  running,
  done,
}: Props) {
  const reachedIndex = done ? ORDER.length : ORDER.indexOf(stage)
  const how = (id: string): 'active' | 'done' | 'idle' => {
    const index = ORDER.indexOf(id)
    if (done) return 'done'
    if (index < reachedIndex) return 'done'
    if (index === reachedIndex && running) return 'active'
    return index <= reachedIndex ? 'done' : 'idle'
  }
  // The loop is live whenever a wave after the first has been dispatched.
  const looping = wave > 1 || (done && waves > 1)

  const badge = (id: string): string => {
    if (id === 'next_wave' && wave > 0) return waves ? `wave ${wave} of ${waves}` : `wave ${wave}`
    if (id === 'convert_file' && expected > 0) return `${landed} of ${expected}`
    return ''
  }

  return (
    <div className="graph-panel">
      <div className="graph-head">
        <strong>The suite graph, as it runs</strong>
        {looping && <span className="graph-laps">{waves} waves</span>}
      </div>
      <svg
        className="graph-svg"
        viewBox={`0 0 342 ${NODES[NODES.length - 1].y + BOX_H + 12}`}
        role="img"
        aria-label="Suite graph: plan, next wave, convert file, finish, with convert file returning to next wave once per wave"
      >
        <defs>
          <marker
            id="sgp-arrow"
            viewBox="0 0 10 10"
            refX="9"
            refY="5"
            markerWidth="6"
            markerHeight="6"
            orient="auto"
          >
            <path d="M0,0 L10,5 L0,10 z" fill="currentColor" />
          </marker>
        </defs>

        {NODES.slice(0, -1).map((node, index) => {
          const next = NODES[index + 1]
          const lit = how(node.id) !== 'idle' && how(next.id) !== 'idle'
          return (
            <line
              key={`edge-${node.id}`}
              className={`graph-edge ${lit ? 'lit' : ''}`}
              x1={BOX_X + BOX_W / 2}
              y1={node.y + BOX_H}
              x2={BOX_X + BOX_W / 2}
              y2={next.y - 2}
              markerEnd="url(#sgp-arrow)"
            />
          )
        })}

        {/* the wave loop: convert_file back up to next_wave */}
        <path
          className={`graph-edge loop ${looping ? 'lit' : ''}`}
          d={`M ${BOX_X} ${184 + BOX_H / 2}
              C 40 ${184 + BOX_H / 2}, 40 ${96 + BOX_H / 2}, ${BOX_X - 2} ${96 + BOX_H / 2}`}
          fill="none"
          markerEnd="url(#sgp-arrow)"
        />
        <text className={`graph-loop-label ${looping ? 'lit' : ''}`} x="34" y="172" textAnchor="middle">
          next wave
        </text>

        {NODES.map((node) => {
          const state = how(node.id)
          const tag = badge(node.id)
          return (
            <g key={node.id} className={`graph-node ${state}`}>
              <rect x={BOX_X} y={node.y} width={BOX_W} height={BOX_H} rx="10" />
              <text className="graph-node-label" x={BOX_X + 12} y={node.y + 20}>
                {node.label}
              </text>
              <text className="graph-node-hint" x={BOX_X + 12} y={node.y + 36}>
                {node.hint}
              </text>
              {tag && (
                <text className="graph-node-badge" x={BOX_X + BOX_W - 12} y={node.y + 20} textAnchor="end">
                  {tag}
                </text>
              )}
            </g>
          )
        })}
      </svg>
      <p className="graph-foot">
        {running ? (
          <>
            <Loader2 size={12} className="spin" /> {stage || 'starting'}
          </>
        ) : done ? (
          `${expected} file${expected === 1 ? '' : 's'} converted across ${waves} wave${waves === 1 ? '' : 's'}`
        ) : (
          'Page objects convert first, then the specs that import them.'
        )}
      </p>
    </div>
  )
}
