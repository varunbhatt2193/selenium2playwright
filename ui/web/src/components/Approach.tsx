import { ArrowRight, Github } from 'lucide-react'
import Brand from './Brand'

type Props = { onOpenLab: () => void }

const REPO = 'https://github.com/varunbhatt2193/selenium2playwright'

const PIPELINE: [string, string, string][] = [
  ['01', 'Intake', 'Classify the file: page object, spec, or something to refuse honestly.'],
  ['02', 'Convert', 'One model writes Playwright, guided by a 28-rule playbook earned from evals.'],
  ['03', 'Validate', 'Four deterministic gates on the written file: tsc, residue scan, ESLint, parity.'],
  ['04', 'Critic', 'A second model reads the draft and the findings. Fail → back to 02, at most three laps.'],
  ['05', 'Report', 'Status, gates, every TODO(review) in one ledger — and for a suite, the tree compiled as one.'],
]

export default function Approach({ onOpenLab }: Props) {
  return (
    <section className="approach-section" id="approach" aria-labelledby="approach-title">
      <div className="section-kicker">03 · How it works</div>
      <div className="approach-layout">
        <div className="approach-copy">
          <h2 id="approach-title">
            The model drafts.
            <br />
            The compiler decides.
          </h2>
          <p>
            A language model is a good writer and an unreliable judge of its own work. So nothing here trusts the
            draft: the same pinned TypeScript toolchain that would run in CI runs on every attempt, and the loop
            only stops when the gates pass or the attempts run out — then it says which.
          </p>
          <div className="impact-callout">
            <span>What the numbers mean</span>
            <strong>
              12/12 on the sample suite, ~20s · 568 offline tests in CI with no model spend · converted code executed
              in a real browser as a fifth measurement
            </strong>
          </div>
          <div className="approach-links">
            <a href={REPO} target="_blank" rel="noreferrer">
              <Github size={15} /> Read the source
            </a>
            <a href={`${REPO}/blob/main/docs/playbook.md`} target="_blank" rel="noreferrer">
              The playbook <ArrowRight size={13} />
            </a>
            <a href={`${REPO}/blob/main/docs/hard-cases.md`} target="_blank" rel="noreferrer">
              The twelve hard cases <ArrowRight size={13} />
            </a>
          </div>
        </div>
        <div className="pipeline">
          {PIPELINE.map(([number, title, text], index) => (
            <div className="pipeline-step" key={number}>
              <span className="step-number">{number}</span>
              <div>
                <strong>{title}</strong>
                <p>{text}</p>
              </div>
              {index < PIPELINE.length - 1 && <span className="step-line" />}
            </div>
          ))}
          <div className="pipeline-loop" aria-hidden="true">
            reflection loop · 04 → 02 · ≤ 3 attempts
          </div>
        </div>
      </div>
      <footer>
        <div>
          <Brand size={16} />
          <strong>Selenium → Playwright</strong>
        </div>
        <p>Designed and engineered by Varun Bhatt · Senior SDET · Python, LangGraph, LangSmith, TypeScript</p>
        <div className="footer-links">
          <button onClick={onOpenLab}>
            Run the demo <ArrowRight size={14} />
          </button>
        </div>
      </footer>
    </section>
  )
}
