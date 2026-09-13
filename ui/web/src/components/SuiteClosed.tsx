import { ArrowRight, CheckCircle2, FileCode2, Github, Lock, PlayCircle } from 'lucide-react'
import { GITHUB } from '../links'
import { Link } from '../router'
import Code from './Code'

// What /suite shows while whole-suite conversion is closed to the public
// (2026-09-12). The upload page is SuiteLab, kept in the repository and not
// routed: reopening it is App.tsx rendering SuiteLab again, plus SUITE_OPEN in
// ui/server.py and "suite" in guard.PUBLIC_GRAPHS. The page going quiet is not
// the block; those two are.
//
// The numbers are the README's, for the 12-file sample suite (step 11.3a).
const SAMPLE_FILES = 12
const SAMPLE_SECONDS = 20

const STEPS = `git clone ${GITHUB}
cd selenium2playwright
uv sync

# the pinned TypeScript toolchain the checks run
(cd sandbox && npm ci)

# add ANTHROPIC_API_KEY, or OPENAI_API_KEY and S2P_MODEL=openai:gpt-5.4
cp .env.example .env

uv run s2p suite path/to/your/selenium-suite --out out/playwright`

export default function SuiteClosed() {
  return (
    <section className="lab-section suite-closed" aria-labelledby="suite-title">
      <div className="section-heading-row">
        <div>
          <h1 id="suite-title">Convert a whole suite</h1>
          <p>Page objects first, then the tests that use them, and the finished folder compiled as one project.</p>
        </div>
      </div>

      <div className="closed-notice" role="note">
        <Lock size={20} />
        <div>
          <strong>Whole-suite conversion is closed on this demo.</strong>
          <p>
            One suite is one click and dozens of model calls: every page object and every spec, each with up to three
            attempts and a second model reviewing them. This demo pays for those tokens itself, so a suite runs on your
            own machine, with your own API key. It is the same agent, with the same checks and the same report.
          </p>
          <p>
            One file at a time still converts here, within the daily limit, on the <Link to="/convert">Single file</Link> page.
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
        <Link to="/" className="how-proof-link">
          <PlayCircle size={14} /> Watch it in the demo <ArrowRight size={14} />
        </Link>
      </div>

      <h2>Run it on your own suite</h2>
      <p className="how-caption">
        You need <a href="https://docs.astral.sh/uv/" target="_blank" rel="noreferrer">uv</a>, Node 22 and a model key.
        The converted folder and a markdown report land in <code>out/playwright</code>.
      </p>
      <div className="closed-steps">
        <Code code={STEPS} language="plain" lineNumbers={false} ariaLabel="Commands to run a suite conversion locally" />
      </div>

      <div className="closed-actions">
        <a className="primary-button" href={GITHUB} target="_blank" rel="noreferrer">
          <Github size={16} /> Clone it from GitHub
        </a>
        <Link to="/convert" className="secondary-button">
          <FileCode2 size={16} /> Convert one file here
        </Link>
      </div>
    </section>
  )
}
