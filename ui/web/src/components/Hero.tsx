import { ArrowDown, CheckCircle2, FolderArchive, ShieldCheck, Sparkles } from 'lucide-react'
import type { Limits } from '../types'
import Ring from './Ring'

type Props = { limits: Limits | null; onOpenLab: () => void; onOpenSuite: () => void }

// The numbers here are the repository's, not the page's: the twelve-file
// sample suite as it converts today (README, step 11.3a), and the test count
// CI runs on every push. Change them there first.
const SAMPLE_FILES = 12
const SAMPLE_SECONDS = 20

export default function Hero({ limits, onOpenLab, onOpenSuite }: Props) {
  const budget = limits?.budget
  return (
    <section className="hero" aria-labelledby="hero-title">
      <div className="hero-noise" />
      <div className="hero-copy">
        <div className="eyebrow">
          <span /> Compiler-verified test migration
        </div>
        <h1 id="hero-title">
          Convert the suite.
          <br />
          <em>Keep the proof.</em>
        </h1>
        <p className="hero-lede">
          A LangGraph agent that converts TypeScript Selenium tests to Playwright — then compiles, lints and
          reviews its own output before you ever see it, going round again with the findings, up to three times.
        </p>
        <div className="hero-actions">
          <button className="primary-button" onClick={onOpenLab}>
            Try a live conversion <ArrowDown size={17} />
          </button>
          <button className="ghost-button" onClick={onOpenSuite}>
            <FolderArchive size={16} /> Upload a whole suite
          </button>
        </div>
        <div className="built-by">
          <span className="avatar">VB</span>
          <span>
            <strong>Built by Varun Bhatt</strong>
            <small>Senior SDET · LangGraph · Playwright</small>
          </span>
        </div>
      </div>

      <div className="hero-proof" aria-label="Project highlights">
        <div className="proof-header">
          <Sparkles size={17} />
          <span>Sample suite, converted live</span>
          <span className="live-dot">Live</span>
        </div>
        <div className="proof-score">
          <Ring value={SAMPLE_FILES} max={SAMPLE_FILES} label={`${SAMPLE_FILES}/${SAMPLE_FILES}`} />
          <div>
            <strong>{SAMPLE_FILES} of {SAMPLE_FILES} files passed all four gates</strong>
            <p>
              Page objects, then the specs that import them · tree compiles as one project · ~{SAMPLE_SECONDS}s
            </p>
          </div>
        </div>
        <div className="proof-list">
          {[
            'Four deterministic gates: tsc, Selenium residue, ESLint, parity',
            'A second model reviews every draft; failures go round again',
            'Every TODO(review) it could not resolve, in one ledger',
          ].map((item) => (
            <div key={item}>
              <CheckCircle2 size={16} />
              <span>{item}</span>
            </div>
          ))}
        </div>
        <div className="proof-footer">
          <ShieldCheck size={15} />
          {budget ? (
            <span>
              {limits?.line} <BudgetBar used={budget.used} limit={budget.limit} />
            </span>
          ) : (
            <span>{limits?.line || 'Runs in the cloud behind a daily budget.'}</span>
          )}
        </div>
      </div>
    </section>
  )
}

function BudgetBar({ used, limit }: { used: number; limit: number }) {
  const pct = limit ? Math.min(100, Math.round((used / limit) * 100)) : 0
  return (
    <i className="budget-bar" aria-hidden="true">
      <b style={{ width: `${pct}%` }} />
    </i>
  )
}
