import { ArrowRight, BookOpen, CheckCircle2, Github } from 'lucide-react'
import { GITHUB } from '../links'
import { STACK } from '../stack'
import { Link } from '../router'

// The page for the person deciding whether to read the source: what the
// pieces are, how they talk, and what the agent does on every file. The
// numbers are the repository's (README, step 11.3a); change them there first.
const SAMPLE_FILES = 12
const SAMPLE_SECONDS = 20

export default function HowItWorks() {
  return (
    <section className="how" aria-labelledby="how-title">
      <div className="section-heading-row">
        <div>
          <h1 id="how-title">How it is built</h1>
          <p>
            A language model is a good writer and an unreliable judge of its own work. So the agent never trusts
            its draft: the same compiler and linter a CI pipeline would run check every attempt, and a second model
            reads the findings before anything reaches you.
          </p>
        </div>
      </div>

      <div className="how-proof">
        <CheckCircle2 size={18} />
        <span>
          <strong>
            {SAMPLE_FILES} of {SAMPLE_FILES} files
          </strong>{' '}
          on the sample suite pass every check and compile together as one project, in about {SAMPLE_SECONDS} seconds.
        </span>
        <Link to="/suite" className="how-proof-link">
          Run it yourself <ArrowRight size={14} />
        </Link>
      </div>

      <h2>The system</h2>
      <p className="how-caption">
        The browser never sees a model key. The page talks to a small FastAPI server, which calls the deployed agent
        as a metered visitor. The agent image carries Node and a pinned TypeScript toolchain, so it can compile what
        it writes.
      </p>
      <SystemDiagram />

      <h2>What happens to every file</h2>
      <p className="how-caption">
        One model converts, four deterministic checks judge the result, a second model reads the findings. Anything
        that fails goes round again with those findings, at most three times, and the report says which.
      </p>
      <LoopDiagram />

      <h2>The stack</h2>
      <div className="stack-grid">
        {STACK.map((group) => (
          <div className="stack-group" key={group.title}>
            <strong>{group.title}</strong>
            <p>{group.blurb}</p>
            <ul>
              {group.items.map(([name, what]) => (
                <li key={name}>
                  <span>{name}</span>
                  <small>{what}</small>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>

      <div className="how-links">
        <a href={GITHUB} target="_blank" rel="noreferrer">
          <Github size={15} /> Read the source
        </a>
        <a href={`${GITHUB}/blob/main/docs/playbook.md`} target="_blank" rel="noreferrer">
          <BookOpen size={15} /> The conversion playbook
        </a>
        <a href={`${GITHUB}/blob/main/docs/architecture.html`} target="_blank" rel="noreferrer">
          The full architecture page <ArrowRight size={13} />
        </a>
      </div>
    </section>
  )
}

// --- diagrams -----------------------------------------------------------------
// Inline SVG, so the boxes take the page's colours and the text stays selectable.
// Coordinates are in a fixed viewBox; the SVG scales to its container.

type BoxProps = { x: number; y: number; w: number; h: number; title: string; lines?: string[]; tone?: 'accent' | 'plain' | 'good' }

function Box({ x, y, w, h, title, lines = [], tone = 'plain' }: BoxProps) {
  const titleY = lines.length ? y + 26 : y + h / 2 + 5
  return (
    <g className={`dbox tone-${tone}`}>
      <rect x={x} y={y} width={w} height={h} rx={12} />
      <text x={x + w / 2} y={titleY} className="dtitle" textAnchor="middle">
        {title}
      </text>
      {lines.map((line, i) => (
        <text key={i} x={x + w / 2} y={titleY + 18 + i * 16} className="dsub" textAnchor="middle">
          {line}
        </text>
      ))}
    </g>
  )
}

function Arrow({ d, label, lx, ly }: { d: string; label?: string; lx?: number; ly?: number }) {
  return (
    <g className="darrow">
      <path d={d} markerEnd="url(#head)" />
      {label && (
        <text x={lx} y={ly} textAnchor="middle" className="dlabel">
          {label}
        </text>
      )}
    </g>
  )
}

function Defs() {
  return (
    <defs>
      <marker id="head" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse">
        <path d="M 0 0 L 10 5 L 0 10 z" className="dhead" />
      </marker>
    </defs>
  )
}

function SystemDiagram() {
  return (
    <svg className="diagram" viewBox="0 0 980 400" role="img" aria-label="System diagram: browser to FastAPI to the LangGraph deployment, which uses a model, a Node toolchain, Postgres and Redis, and reports to LangSmith">
      <Defs />
      {/* row one: the request path */}
      <Box x={20} y={40} w={200} h={78} title="Browser" lines={['React 19 · Vite · TypeScript', 'this page']} />
      <Arrow d="M 220 79 H 288" label="fetch + SSE" lx={254} ly={68} />
      <Box x={290} y={40} w={200} h={78} title="FastAPI" lines={['ui/server.py', 'validates, streams progress']} />
      <Arrow d="M 490 79 H 558" label="visitor key" lx={524} ly={68} />
      <Box x={560} y={24} w={400} h={110} title="LangGraph deployment" lines={['Docker on Fly.io', 'auth · per-visitor limits · dollar budget', 'convert graph · suite graph']} tone="accent" />

      {/* row two: what the deployment uses */}
      <Arrow d="M 640 134 V 196" />
      <Arrow d="M 760 134 V 196" />
      <Arrow d="M 880 134 V 196" />
      <Box x={548} y={198} w={184} h={92} title="Model" lines={['OpenAI or Anthropic', 'through LangChain', 'actor + critic roles']} />
      <Box x={744} y={198} w={110} h={92} title="Toolchain" lines={['Node · tsc', 'ESLint', 'Playwright rules']} tone="good" />
      <Box x={866} y={198} w={100} h={92} title="Storage" lines={['Postgres', '+ pgvector', 'Redis']} />

      {/* observability, off to the side */}
      <Arrow d="M 560 100 H 500 V 240 H 462" label="traces · feedback" lx={470} ly={172} />
      <Box x={262} y={198} w={200} h={84} title="LangSmith" lines={['every run traced', 'evals and 👎 land as datasets']} />

      {/* the legend line */}
      <text x={20} y={340} className="dnote">
        Storage: Postgres holds run checkpoints and long-term memory (pgvector); Redis holds the run queue and the atomic
      </text>
      <text x={20} y={358} className="dnote">
        counters behind the limits. The toolchain is pinned in the image, so the agent compiles its own output where it runs.
      </text>
    </svg>
  )
}

function LoopDiagram() {
  return (
    <svg className="diagram" viewBox="0 0 980 262" role="img" aria-label="Agent loop: intake, which can refuse; recall; risk review, which can pause to ask you; convert; validate with four gates; critic; report; with failures going back to convert at most three times">
      <Defs />
      <Box x={12} y={52} w={108} h={86} title="Intake" lines={['page object', 'or spec?']} />
      <Arrow d="M 120 95 H 134" />
      <Box x={136} y={52} w={108} h={86} title="Recall" lines={['conventions', 'you taught it']} />
      <Arrow d="M 244 95 H 258" />
      <Box x={260} y={52} w={130} h={86} title="Risk review" lines={['pauses to ask you', 'when a pattern has', 'two right answers']} />
      <Arrow d="M 390 95 H 404" />
      <Box x={406} y={52} w={118} h={86} title="Convert" lines={['one model writes', 'the Playwright', 'version']} tone="accent" />
      <Arrow d="M 524 95 H 538" />
      <Box x={540} y={48} w={168} h={94} title="Validate · 4 gates" lines={['tsc compiles it', 'no Selenium left', 'ESLint · parity']} tone="good" />
      <Arrow d="M 708 95 H 722" />
      <Box x={724} y={52} w={124} h={86} title="Critic" lines={['a second model', 'reads draft', 'and findings']} />
      <Arrow d="M 848 95 H 862" />
      <Box x={864} y={52} w={104} h={86} title="Report" lines={['status · gates', 'TODO ledger']} />

      {/* the honest exit: not a file it can convert */}
      <Arrow d="M 66 138 V 176" label="not Selenium TS" lx={132} ly={162} />
      <Box x={12} y={178} w={108} h={60} title="Refuse" lines={['says why, stops']} />

      {/* the loop back */}
      <Arrow d="M 786 138 V 206 H 465 V 140" label="fail → convert again · at most 3 attempts" lx={625} ly={230} />
    </svg>
  )
}
