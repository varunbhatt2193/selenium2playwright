import { ArrowRight, CheckCircle2, Github } from 'lucide-react'
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
          Run it on your suite <ArrowRight size={14} />
        </Link>
      </div>

      <h2>The system</h2>
      <p className="how-caption">
        What runs where, and what crosses each wire. The browser never sees a model key: it talks to the playground
        app, which screens the input and calls the agent server as a metered visitor. Below, the eval harness and the
        CI gate decide what gets deployed.
      </p>
      <a className="architecture" href="/architecture.svg" target="_blank" rel="noreferrer">
        <img
          src="/architecture.svg"
          width={1280}
          height={1052}
          alt="Architecture: a browser calls the playground app on Fly, which screens input and calls the LangGraph API server with a visitor key; the server holds the guard, the convert and suite graphs and a pinned Node toolchain, keeps state in Postgres and Redis on a private network, and calls model providers and LangSmith; a developer machine runs the CLI, eval harness and deploy scripts, and GitHub runs the CI gate"
        />
        <span>
          Open full size <ArrowRight size={13} />
        </span>
      </a>

      <WhyNotClaudeCode />

      <h2>What happens to every file</h2>
      <p className="how-caption">
        One model converts, four deterministic checks judge the result, a second model reads the findings. Anything
        that fails goes round again with those findings, at most three times, and the report says which.
      </p>
      <LoopDiagram />

      <h2>The Tech Stack</h2>
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
      </div>
    </section>
  )
}

// --- why an agent ---------------------------------------------------------------
// The first question a technical visitor asks, answered where they will see it.
// Same argument as the README's "Why an agent" table; change both together.

const COMPARISON: [string, string, string][] = [
  [
    'Checking the output',
    'Runs tsc or the linter when it decides to, and decides for itself when it is done.',
    'Compile, lint, residue and parity checks run on every attempt. They are steps in the graph, not a choice the model makes. A failure goes back with its findings, at most three times, before you see any code.',
  ],
  [
    'Tests that go missing',
    'A test or an assertion can vanish in translation and the file still compiles. Someone has to notice.',
    'Test and assertion counts are compared with the source. A mismatch sends the file back for repair, never a silent drop. So does any import the source never had.',
  ],
  [
    'Quality',
    'Depends on the prompt and the day. Nobody measures it.',
    'Scored on a fixed evaluation set, and a prompt change ships only after an A/B run on that set. CI replays the golden fixtures in a real browser on every push.',
  ],
  [
    'Scale',
    'File by file, with someone watching each one.',
    'A whole suite in one command: page objects first, then the tests that use them, compiled together as one project.',
  ],
  [
    'Token spend',
    'Explores the repo to build its own context: every file it opens and every retry is tokens, and the same job costs a different amount each time.',
    'One Selenium file per call, plus the page objects it imports and a capped slice of its callers. Helpers and config are copied across with no model call, and the four checks are a compiler, a linter and two scripts, which cost nothing.',
  ],
  [
    'Who can run it',
    'Someone with prompting skill and access to the repo.',
    'Anyone, with the same result: this page, the CLI, or a CI pipeline.',
  ],
  [
    'What reaches the model',
    'Whatever is in the files, including text written to steer the model.',
    'Anything that is not Selenium, or that talks to the model, is refused before a model sees it. Every run is metered against a budget.',
  ],
]

function WhyNotClaudeCode() {
  return (
    <>
      <h2 id="why-not-claude-code">Why not just Claude Code in the repo?</h2>
      <p className="how-caption">
        Fair question. Claude Code can convert a Selenium file, and for one file it does it well. The difference is
        what you can trust when nobody reviews every file: when the migration is repeated, large, or has to be right.
      </p>
      <div className="compare-wrap">
        <table className="compare">
          <thead>
            <tr>
              <th scope="col">
                <span className="sr-only">Concern</span>
              </th>
              <th scope="col">Claude Code in the repo</th>
              <th scope="col" className="compare-us">
                This agent
              </th>
            </tr>
          </thead>
          <tbody>
            {COMPARISON.map(([concern, repo, agent]) => (
              <tr key={concern}>
                <th scope="row">{concern}</th>
                <td data-label="Claude Code in the repo">{repo}</td>
                <td data-label="This agent" className="compare-us">
                  {agent}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="how-note">
        For a one-off file, Claude Code in the repo is genuinely fine. An agent earns its place when the job is
        repeated, large, or needs guarantees. Closing the gap between &ldquo;the model can do it&rdquo; and &ldquo;a
        system you can trust unattended&rdquo; is the engineering this project is about.
      </p>
    </>
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

function LoopDiagram() {
  return (
    <svg className="diagram" viewBox="0 0 980 262" role="img" aria-label="Agent loop: intake, which can refuse; recall; risk review, which flags a pattern with two right answers and asks you when run locally; convert; validate with four gates; critic; report; with failures going back to convert at most three times">
      <Defs />
      <Box x={12} y={52} w={108} h={86} title="Intake" lines={['page object', 'or spec?']} />
      <Arrow d="M 120 95 H 134" />
      <Box x={136} y={52} w={108} h={86} title="Recall" lines={['conventions', 'you taught it']} />
      <Arrow d="M 244 95 H 258" />
      <Box x={260} y={52} w={130} h={86} title="Risk review" lines={['flags a pattern with', 'two right answers; asks', 'you when run locally']} />
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
