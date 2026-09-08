import { ArrowRight, BookOpen, FlaskConical, Gauge, Scale, ThumbsDown } from 'lucide-react'
import { GITHUB } from '../links'
import { AB, EXECUTION, GAPS, HARD, JUDGE, REPORTS, SHOOTOUT, SHOOTOUT_TOTAL } from '../evals'

const doc = (path: string) => `${GITHUB}/blob/main/${path}`

// The page for an evaluation role: not "does it work" but "how do you know,
// and how sure are you". Every figure is in evals.ts with the report it came from.
export default function Evaluation() {
  return (
    <section className="how evals" aria-labelledby="evals-title">
      <div className="section-heading-row">
        <div>
          <h1 id="evals-title">Evaluation</h1>
          <p>
            The rule this project runs on: <strong>no prompt change without a green eval run.</strong> A pinned
            dataset, a pinned code revision, receipts for every number, and the same care about noise as about
            improvement. This page is the evidence, with a link to each report.
          </p>
        </div>
      </div>

      {/* --- the ladder ------------------------------------------------------- */}
      <h2>Three ways of measuring, each seeing what the last cannot</h2>
      <p className="how-caption">
        Four exact checks can all pass while a converted test quietly checks nothing. So there are three layers, and
        the last one is a browser.
      </p>
      <div className="ladder">
        <div className="rung">
          <span className="rung-n">1</span>
          <Gauge size={18} />
          <strong>Deterministic gates</strong>
          <p>tsc compiles it, no Selenium residue, ESLint with Playwright rules, parity: no test or public member lost.</p>
          <small>Exact, free, runs on every attempt. Cannot judge style, cannot judge behaviour.</small>
        </div>
        <div className="rung">
          <span className="rung-n">2</span>
          <Scale size={18} />
          <strong>Calibrated LLM judge</strong>
          <p>An openevals rubric scores 1 to 5 for idiomatic Playwright: user-facing locators, web-first assertions, POM shape.</p>
          <small>Trusted only after it passed calibration, below. Two judge models, checked against each other.</small>
        </div>
        <div className="rung">
          <span className="rung-n">3</span>
          <FlaskConical size={18} />
          <strong>Execution in a real browser</strong>
          <p>Saved conversions replayed through Chromium against a pinned demo app. The browser gets a vote.</p>
          <small>Runs in CI on every push with zero model spend, because finished experiments already hold the code.</small>
        </div>
      </div>

      {/* --- judge calibration ------------------------------------------------ */}
      <h2>The judge was calibrated before it was trusted</h2>
      <p className="how-caption">
        A judge that has not been checked is one more opinion. Three checks on hand-written goldens, then two
        different judge models scored the same rows.
      </p>
      <div className="tile-row">
        <Tile value={`${JUDGE.goldens}/${JUDGE.goldens}`} label="goldens scored 5 of 5" note="both judges, mean 5.0" />
        <Tile value={JUDGE.brokenLowerGpt} label="broken goldens scored lower" note={`gpt-5.4, all ${JUDGE.brokenVariants} · Opus ${JUDGE.brokenLowerOpus}, 6 replies cut short`} />
        <Tile value="24/24" label="repeat pairs agreed" note="same file judged twice" />
        <Tile value={JUDGE.exactPct} label="exact agreement, two judges" note={`${JUDGE.withinOnePct} within one point · ${JUDGE.rowsBothScored} rows`} />
      </div>
      <p className="how-note">
        The two judges were {JUDGE.twoJudges}. One provider cut about two in five judge replies short mid-rubric; the
        fix recovers the verdict from the mandatory closing sentence, retries, and <em>counts</em> what is still
        unscored instead of dropping it. Recorded as gap T11.{' '}
        <a href={doc(REPORTS.judge)} target="_blank" rel="noreferrer">
          Report <ArrowRight size={12} />
        </a>
      </p>

      {/* --- the A/B and the shootout ----------------------------------------- */}
      <h2>Does the repair loop earn its extra calls?</h2>
      <p className="how-caption">
        One controlled variable: one conversion attempt versus up to three. Same twelve pinned files, same code
        revision, same critic model reviewing every draft. Three actor sizes.
      </p>
      <ShootoutChart />
      <div className="table-scroll">
        <table className="file-table evals-table">
          <thead>
            <tr>
              <th>actor</th>
              <th>one attempt</th>
              <th>with reflection</th>
              <th>files repaired</th>
              <th>cost, one → reflection</th>
            </tr>
          </thead>
          <tbody>
            {SHOOTOUT.map((r) => (
              <tr key={r.actor}>
                <td>
                  <strong>{r.actor}</strong> <small>{r.size}</small>
                </td>
                <td>
                  {r.one}/{SHOOTOUT_TOTAL}
                </td>
                <td>
                  {r.reflection}/{SHOOTOUT_TOTAL}
                </td>
                <td>{r.repaired}</td>
                <td>
                  {r.costOne} → {r.costReflection}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="finding-grid">
        <div className="finding">
          <strong>What the loop did</strong>
          <p>
            For the small model it fixed every compile failure: {SHOOTOUT[0].one} to {SHOOTOUT[0].reflection} of twelve. For the
            large model, {SHOOTOUT[2].one} to {SHOOTOUT[2].reflection}, at {AB.actorTokens} the actor tokens and {AB.wallClock} the
            wall-clock. That trade is the production default.
          </p>
        </div>
        <div className="finding">
          <strong>What it did not do</strong>
          <p>
            Sonnet went {SHOOTOUT[1].one} to {SHOOTOUT[1].reflection}, and several of Opus's "improvements" were the critic
            changing its mind between runs, not better code. With twelve rows and one run per arm, a delta of one or two
            is variance. The reports say so, in those words.
          </p>
        </div>
      </div>
      <p className="how-note">
        <a href={doc(REPORTS.ab)} target="_blank" rel="noreferrer">
          The A/B <ArrowRight size={12} />
        </a>
        <a href={doc(REPORTS.shootout)} target="_blank" rel="noreferrer">
          The shootout <ArrowRight size={12} />
        </a>
      </p>

      {/* --- the hard cases ---------------------------------------------------- */}
      <h2>A benchmark the converter was bad at, on purpose</h2>
      <p className="how-caption">
        The ordinary dataset passed, which proves little: ordinary cases are the ones where a careful translation is
        also the obvious one. So a second suite was built from the twelve patterns an SDET loses sleep over: stale
        elements, nested frames, window handles, custom polling, DOM-mutating loops, promise chains with no await.
        Eleven rows, because two of the twelve already live in the ordinary set. Each with an independently written
        Playwright golden, green in a real browser. <strong>This benchmark ran on {HARD.model}</strong>, not the Claude
        models above, so the two sections are not comparable.
      </p>
      <div className="tile-row">
        <Tile value={HARD.baselinePassed} label="passed, baseline arm" note={`a repeat of the same setup scored ${HARD.firstBaseline}: that gap is the noise floor`} />
        <Tile value={HARD.finalPassed} label="passed after two rule changes" note={`playbook rules ${HARD.rulesAdded.join(' and ')}, each gated by a run`} tone="good" />
        <Tile value="2" label="cases held out of tuning" note={HARD.reserved} />
        <Tile value="1" label="still unsolved" note={HARD.unsolved} tone="warn" />
      </div>
      <p className="how-note">
        Each new rule is recorded against the rows it was written for, so the report cannot describe a held-out case
        as "tuned for". One of the two held-out cases improved anyway; the report calls that likely variance, not
        generalisation.{' '}
        <a href={doc(REPORTS.hardCasesScored)} target="_blank" rel="noreferrer">
          Report <ArrowRight size={12} />
        </a>
      </p>

      {/* --- execution --------------------------------------------------------- */}
      <h2>Then the code was run</h2>
      <div className="finding-grid">
        <div className="finding">
          <strong>The number</strong>
          <p>
            {EXECUTION.baseSet} on the base set. On the hard set, {EXECUTION.hardProbe} for the probe run and{' '}
            {EXECUTION.hardArms} for the three tuning arms: the rule changes moved the graph number and left this one
            flat. Split by kind it stops being noisy: {EXECUTION.testRows}; {EXECUTION.pageObjectRows}. A page object is executed against a caller it was
            never shown, so half its failures are a name the converter could not have guessed. That is a measurement
            limit, and it is written down as one.
          </p>
        </div>
        <div className="finding">
          <strong>What only running it could catch</strong>
          <p>
            All four gates green, and the test lost a race: {EXECUTION.caught}. Two independent samples make it a defect
            class, not variance. Logged as a candidate rule and <em>not</em> added to the playbook, because this project
            does not edit the rulebook to fit a fixture it just read.
          </p>
        </div>
      </div>
      <p className="how-note">
        Spend for the whole step: {EXECUTION.spend}. Goldens are immutable. Every figure above comes from a JSON receipt
        rendered by a script, so no number on this page was typed from memory.{' '}
        <a href={doc(REPORTS.execution)} target="_blank" rel="noreferrer">
          Report <ArrowRight size={12} />
        </a>
      </p>

      {/* --- the flywheel and the gaps ----------------------------------------- */}
      <h2>What the evals still cannot see</h2>
      <div className="finding-grid">
        <div className="finding">
          <strong>
            <ThumbsDown size={15} /> The feedback flywheel
          </strong>
          <p>
            A thumbs-down on the public page does not just get recorded. The input is queued as an unreviewed dataset
            row, because a real visitor saying a real file came out wrong is the most valuable signal this project can
            collect.
          </p>
        </div>
        <div className="finding">
          <strong>
            <BookOpen size={15} /> The gap log
          </strong>
          <ul className="gap-list">
            {GAPS.map(([id, text]) => (
              <li key={id}>
                <code>{id}</code> {text}
              </li>
            ))}
          </ul>
        </div>
      </div>

      <div className="how-links">
        <a href={doc(REPORTS.primer)} target="_blank" rel="noreferrer">
          <BookOpen size={15} /> Evaluation primer
        </a>
        <a href={doc(REPORTS.gapLog)} target="_blank" rel="noreferrer">
          The gap log <ArrowRight size={13} />
        </a>
        <a href={doc(REPORTS.hardCases)} target="_blank" rel="noreferrer">
          The twelve hard cases <ArrowRight size={13} />
        </a>
        <a href={doc(REPORTS.agreement)} target="_blank" rel="noreferrer">
          Judge agreement table <ArrowRight size={13} />
        </a>
      </div>
    </section>
  )
}

function Tile({ value, label, note, tone = 'idle' }: { value: string; label: string; note: string; tone?: 'good' | 'warn' | 'idle' }) {
  return (
    <div className={`stat tile tone-${tone}`}>
      <strong>{value}</strong>
      <span>{label}</span>
      <small>{note}</small>
    </div>
  )
}

// One attempt vs reflection, per actor. Grouped bars, two series in fixed
// colours (amber = one attempt, teal = reflection; validated for colour-vision
// deficiency against the chart surface), direct labels, legend, and the table
// above as the accessible view.
function ShootoutChart() {
  const W = 720
  const H = 300
  const left = 44
  const right = 20
  const top = 34
  const bottom = 46
  const plotW = W - left - right
  const plotH = H - top - bottom
  const groupW = plotW / SHOOTOUT.length
  const barW = 34
  const gap = 2
  const y = (v: number) => top + plotH - (v / SHOOTOUT_TOTAL) * plotH
  const ticks = [0, 3, 6, 9, 12]

  return (
    <figure className="chart">
      <div className="chart-head">
        <strong>Files fully passed, of {SHOOTOUT_TOTAL}</strong>
        <div className="legend" aria-hidden="true">
          <span>
            <i className="swatch one" /> one attempt
          </span>
          <span>
            <i className="swatch refl" /> with reflection, at most 3 attempts
          </span>
        </div>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Grouped bar chart of files fully passed out of twelve, one attempt versus reflection, for Haiku, Sonnet and Opus. Values are in the table above.">
        {ticks.map((t) => (
          <g key={t} className="grid">
            <line x1={left} x2={W - right} y1={y(t)} y2={y(t)} />
            <text x={left - 8} y={y(t) + 4} textAnchor="end">
              {t}
            </text>
          </g>
        ))}
        {SHOOTOUT.map((r, i) => {
          const cx = left + groupW * i + groupW / 2
          const x1 = cx - barW - gap / 2
          const x2 = cx + gap / 2
          return (
            <g key={r.actor}>
              <Bar x={x1} v={r.one} y={y} base={y(0)} w={barW} cls="one" title={`${r.actor}, one attempt: ${r.one} of ${SHOOTOUT_TOTAL}`} />
              <Bar x={x2} v={r.reflection} y={y} base={y(0)} w={barW} cls="refl" title={`${r.actor}, with reflection: ${r.reflection} of ${SHOOTOUT_TOTAL}`} />
              <text className="xlabel" x={cx} y={H - 18} textAnchor="middle">
                {r.actor}
              </text>
              <text className="xsub" x={cx} y={H - 4} textAnchor="middle">
                {r.size} model
              </text>
            </g>
          )
        })}
        <line className="baseline" x1={left} x2={W - right} y1={y(0)} y2={y(0)} />
      </svg>
      <figcaption>Same Opus critic on every draft. Costs and repair counts in the table below.</figcaption>
    </figure>
  )
}

function Bar({ x, v, y, base, w, cls, title }: { x: number; v: number; y: (v: number) => number; base: number; w: number; cls: string; title: string }) {
  const topY = y(v)
  const h = Math.max(0, base - topY)
  const r = Math.min(4, h)
  // Rounded at the data end only; square on the baseline.
  const d = `M ${x} ${base} V ${topY + r} Q ${x} ${topY} ${x + r} ${topY} H ${x + w - r} Q ${x + w} ${topY} ${x + w} ${topY + r} V ${base} Z`
  return (
    <g className={`bar ${cls}`}>
      <title>{title}</title>
      <rect className="hit" x={x - 4} y={y(SHOOTOUT_TOTAL)} width={w + 8} height={base - y(SHOOTOUT_TOTAL)} />
      <path d={d} />
      <text x={x + w / 2} y={topY - 7} textAnchor="middle">
        {v}
      </text>
    </g>
  )
}
