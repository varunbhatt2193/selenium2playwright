import { useRef, useState } from 'react'
import { Check, Download, FolderArchive, Layers, Loader2, Play, ShieldCheck, Sparkles, X } from 'lucide-react'
import { convertSuite, download, planSuite, sampleSuite, suiteZip } from '../api'
import type { FileRow, Limits, PlanResponse, SuiteResult } from '../types'
import Code from './Code'

type Props = { limits: Limits | null; onSpent: () => void }
type Status = 'idle' | 'planning' | 'running' | 'done'
type ResultTab = 'files' | 'todos' | 'report'

// The two knobs the page used to show, fixed at the values the sample suite
// converts with: four files at a time, up to three attempts each.
const PARALLEL = 4
const ATTEMPTS = 3

export default function SuiteLab({ limits, onSpent }: Props) {
  const [planned, setPlanned] = useState<PlanResponse | null>(null)
  const [status, setStatus] = useState<Status>('idle')
  const [dragging, setDragging] = useState(false)
  const [error, setError] = useState('')
  const [rows, setRows] = useState<FileRow[]>([])
  // Two different things, shown in two different places. `stages` is the story
  // — what was found, which wave is converting what — and it only grows.
  // `progress` is the last file to land, which changes twelve times and used
  // to overwrite the stage line the moment it appeared.
  const [stages, setStages] = useState<string[]>([])
  const [progress, setProgress] = useState('')
  const [expected, setExpected] = useState(0)
  const [result, setResult] = useState<SuiteResult | null>(null)
  const [tab, setTab] = useState<ResultTab>('files')
  const [reading, setReading] = useState('')
  const [zipping, setZipping] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  const running = status === 'running'
  const budgetOut = Boolean(limits?.budget && limits.budget.remaining <= 0)

  async function plan(files: File[]) {
    if (!files.length) return
    setStatus('planning')
    setError('')
    setResult(null)
    setRows([])
    try {
      setPlanned(await planSuite(files, ''))
    } catch (err) {
      setPlanned(null)
      setError((err as Error).message)
    } finally {
      setStatus('idle')
    }
  }

  async function loadSample() {
    setStatus('planning')
    setError('')
    setResult(null)
    setRows([])
    try {
      setPlanned(await sampleSuite(''))
    } catch (err) {
      setPlanned(null)
      setError((err as Error).message)
    } finally {
      setStatus('idle')
    }
  }

  async function run() {
    if (!planned) return
    setStatus('running')
    setError('')
    setRows([])
    setResult(null)
    setStages([planned.plan.found])
    setProgress('Sending the suite to the agent…')
    setExpected(planned.plan.files)
    try {
      for await (const event of convertSuite({
        tree: planned.tree,
        only: '',
        parallel: PARALLEL,
        attempts: ATTEMPTS,
        model: '',
      })) {
        if (event.kind === 'start') {
          setExpected(event.files)
          setStages([event.found])
        } else if (event.kind === 'node') setStages((s) => [...s, event.label])
        else if (event.kind === 'file') {
          setRows((r) => [...r, event.row])
          // No `landed/of` prefix: `.progress-count` at the end of the same line
          // already carries it, and the line read "2/12 · … 2/12".
          setProgress(`${event.row.path} — ${event.row.status}`)
        } else if (event.kind === 'done') {
          setResult(event.result)
          setTab('files')
          const first = Object.keys(event.result.tree).sort()[0]
          setReading(first || '')
        } else if (event.kind === 'error') setError(event.message)
      }
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setStatus('done')
      onSpent()
    }
  }

  async function getZip() {
    if (!result) return
    setZipping(true)
    try {
      download('playwright-suite.zip', await suiteZip(result.tree, result.markdown))
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setZipping(false)
    }
  }

  const p = planned?.plan
  const blocked = planned?.unaffordable || ''
  const counts = result?.totals ?? {}

  return (
    <section className="lab-section suite-section" aria-labelledby="suite-title" id="suite">
      <div className="section-heading-row">
        <div>
          <h1 id="suite-title">Convert a whole suite</h1>
          <p>Drop a zip of your Selenium folder, or load the sample suite, and get a Playwright folder back.</p>
          {limits?.line && (
            <p className="page-meter">
              <ShieldCheck size={13} /> {limits.line}
            </p>
          )}
        </div>
        <button className="secondary-button" onClick={loadSample} disabled={running || status === 'planning'}>
          <Sparkles size={16} /> Use the 12-file sample suite
        </button>
      </div>

      <div className="lab-card">
        <div className="suite-grid">
          <div className="suite-input">
            <div
              className={`dropzone wide ${dragging ? 'dragging' : ''} ${running ? 'disabled' : ''}`}
              onDragOver={(e) => {
                e.preventDefault()
                if (!running) setDragging(true)
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => {
                e.preventDefault()
                setDragging(false)
                if (!running) plan(Array.from(e.dataTransfer.files ?? []))
              }}
              onClick={() => !running && fileRef.current?.click()}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => e.key === 'Enter' && fileRef.current?.click()}
            >
              <input
                ref={fileRef}
                type="file"
                multiple
                accept=".zip,.ts,.tsx,.js,.mjs,.cjs,application/zip"
                onChange={(e) => plan(Array.from(e.target.files ?? []))}
                hidden
              />
              <FolderArchive size={28} />
              <strong>Drop a zip of your Selenium folder</strong>
              <span>or its files · a zip keeps the folder structure the imports need</span>
            </div>

            <ul className="suite-how">
              <li>Page objects convert first, then the specs that import them.</li>
              <li>The converted folder is compiled as one project, not file by file.</li>
              <li>The report comes back inside the zip.</li>
            </ul>
          </div>

          <aside className="coverage-pane plan-pane" aria-label="Suite plan">
            <div className="coverage-heading">
              <span>
                <Layers size={14} /> Plan, before any spend
              </span>
              {p && <span className="coverage-badge">{p.files} to convert</span>}
            </div>
            {status === 'planning' && (
              <div className="plan-empty">
                <Loader2 size={16} className="spin" /> Scanning…
              </div>
            )}
            {!p && status !== 'planning' && (
              <div className="plan-empty">
                Drop the files, or load the sample suite, to see the plan: what converts, what is carried across
                untouched, and what it costs of today's budget.
              </div>
            )}
            {p && (
              <>
                <p className="plan-found">{p.found}</p>
                <p className="plan-line">{p.line}</p>
                <ol className="waves">
                  {p.waves.map((wave, i) => (
                    <li key={i}>
                      <strong>Wave {i + 1}</strong>
                      {p.wave_lines[i] && <span className="wave-what">{p.wave_lines[i]}</span>}
                      <ul>
                        {wave.map((path) => (
                          <li key={path}>
                            <code>{path}</code>
                          </li>
                        ))}
                      </ul>
                    </li>
                  ))}
                </ol>
                {p.copied.length > 0 && (
                  <p className="plan-note">
                    Carried across untouched: {p.copied.map((c) => <code key={c}>{c}</code>)}
                  </p>
                )}
                {p.skipped.length > 0 && (
                  <details className="trail-details">
                    <summary>Skipped — {p.skipped.length}</summary>
                    <ul className="todo-list plain">
                      {p.skipped.map(([path, why]) => (
                        <li key={path}>
                          <code>{path}</code> — {why}
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
                {p.notes.length > 0 && (
                  <div className="plan-limit">
                    {p.notes.map((note, i) => (
                      <p key={i}>{note}</p>
                    ))}
                  </div>
                )}
                <p className="plan-cost">
                  Costs <strong>{p.billable}</strong> of today's conversions.
                </p>
                {blocked && <div className="error-box soft">{blocked}</div>}
              </>
            )}
            <button
              className="run-button wide"
              onClick={run}
              disabled={!p || running || Boolean(blocked) || budgetOut || status === 'planning'}
            >
              {running ? <Loader2 size={14} className="spin" /> : <Play size={14} fill="currentColor" />}
              {running ? 'Converting the suite…' : p ? `Convert ${p.files} file${p.files === 1 ? '' : 's'}` : 'Convert suite'}
            </button>
          </aside>
        </div>

        {(running || rows.length > 0 || error || result) && (
          <div className="suite-progress">
            {running && stages.length > 0 && <Stages stages={stages} />}
            {running && (
              <div className="progress-line">
                <Loader2 size={14} className="spin" /> {progress}
                <span className="progress-count">
                  {rows.length}/{expected || '?'}
                </span>
              </div>
            )}
            {error && (
              <div className="error-box" role="alert">
                <X size={16} />
                <span>{error}</span>
              </div>
            )}
            {rows.length > 0 && !result && <FileTable rows={rows} />}
          </div>
        )}

        {result && (
          <div className="suite-result">
            <div className={`result-banner ${result.passed ? 'ok' : 'warn'}`}>
              <span>
                {result.passed ? <Check size={16} /> : <ShieldCheck size={16} />}
                <strong>{result.passed ? 'Passed.' : 'Needs review.'}</strong> {result.headline}
              </span>
              {Object.keys(result.tree).length > 0 && (
                <button className="primary-button small" onClick={getZip} disabled={zipping}>
                  {zipping ? <Loader2 size={14} className="spin" /> : <Download size={15} />}
                  Download the converted suite · {Object.keys(result.tree).length} files + report
                </button>
              )}
            </div>

            <div className="stat-row">
              <Stat label="Passed" value={`${counts.passed ?? 0}/${result.rows.length}`} tone={result.rows.length && counts.passed === result.rows.length ? 'good' : 'warn'} />
              <Stat label="Needs review" value={String(counts['needs-review'] ?? 0)} tone={counts['needs-review'] ? 'warn' : 'idle'} />
              <Stat
                label="Converted files compile"
                value={result.compiles ? 'YES' : 'NO'}
                tone={result.compiles ? 'good' : 'bad'}
              />
              <Stat label="Elapsed" value={`${Math.round(result.elapsed)}s`} tone="idle" />
            </div>

            <div className="code-tabs" role="tablist">
              {(['files', 'todos', 'report'] as ResultTab[]).map((t) => (
                <button key={t} className={tab === t ? 'active' : ''} onClick={() => setTab(t)} role="tab">
                  {t === 'files' ? 'Files' : t === 'todos' ? `TODOs · ${result.todos.length}` : 'Report'}
                </button>
              ))}
            </div>

            {tab === 'files' && (
              <>
                <FileTable rows={result.rows} />
                {Object.keys(result.tree).length > 0 && (
                  <div className="read-one">
                    <div className="code-topline">
                      <select value={reading} onChange={(e) => setReading(e.target.value)} aria-label="Read one converted file">
                        {Object.keys(result.tree)
                          .sort()
                          .map((path) => (
                            <option key={path} value={path}>
                              {path}
                            </option>
                          ))}
                      </select>
                      <span>converted</span>
                    </div>
                    {reading && result.tree[reading] !== undefined && <Code code={result.tree[reading]} language="typescript" />}
                  </div>
                )}
              </>
            )}

            {tab === 'todos' && (
              <div className="tab-body">
                {result.todos.length ? (
                  <ul className="todo-list">
                    {result.todos.map(([text, places], i) => (
                      <li key={i}>
                        {text}
                        <small>{places.join(', ')}</small>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="muted-copy">No TODO(review) items anywhere in the suite.</p>
                )}
                {result.notes.length > 0 && (
                  <ul className="todo-list plain">
                    {result.notes.map((note, i) => (
                      <li key={i}>{note}</li>
                    ))}
                  </ul>
                )}
              </div>
            )}

            {tab === 'report' && (
              <div className="tab-body">
                {result.compiles ? (
                  <div className="review-note ok">
                    <Check size={16} />
                    <span>
                      <strong>{result.tree_files} files compile together.</strong>
                      <small>
                        The converted folder was compiled as one project. No findings from tsc in anything this run
                        produced.
                      </small>
                    </span>
                  </div>
                ) : result.tree_error ? (
                  <div className="error-box soft">The tree could not be compiled: {result.tree_error}</div>
                ) : (
                  <>
                    <div className="error-box soft">
                      The converted files do not compile as one project — {result.tree_findings_mine.length} finding(s).
                    </div>
                    <Code code={result.tree_findings_mine.join('\n')} language="plain" lineNumbers={false} />
                  </>
                )}

                {result.tree_findings_absent.length > 0 && (
                  <details className="trail-details">
                    <summary>
                      {result.tree_findings_absent.length} error(s) from packages this sandbox does not install — not
                      counted above
                    </summary>
                    <p className="muted-copy">
                      The sandbox carries TypeScript and Playwright and nothing else. Imports like <code>zod</code> or{' '}
                      <code>mysql2</code> cannot resolve here and would resolve in your own checkout.
                    </p>
                    <Code code={result.tree_findings_absent.join('\n')} language="plain" lineNumbers={false} />
                  </details>
                )}

                {result.tree_findings_carried.length > 0 && (
                  <details className="trail-details">
                    <summary>
                      {result.tree_findings_carried.length} error(s) in files carried across unconverted — not counted
                      above
                    </summary>
                    <p className="muted-copy">
                      These files are still Selenium, and there is no Selenium in a Playwright project, so they cannot
                      compile and were never expected to. Convert them and the errors go with them.
                    </p>
                    <Code code={result.tree_findings_carried.join('\n')} language="plain" lineNumbers={false} />
                  </details>
                )}

                <p className="muted-copy">
                  Parity: what the source exposed publicly, and what became of it. A rename is a guess; a removal with
                  no reason is the line worth reading.
                </p>
                <div className="stat-row">
                  <Stat label="Kept" value={String(result.kept)} tone="good" />
                  <Stat label="Renamed" value={String(result.renamed)} tone={result.renamed ? 'warn' : 'idle'} />
                  <Stat label="Removed" value={String(result.removed)} tone={result.removed ? 'warn' : 'idle'} />
                  <Stat label="Unexplained" value={String(result.unexplained)} tone={result.unexplained ? 'bad' : 'idle'} />
                </div>
                {result.losses.length > 0 && (
                  <div className="table-scroll">
                    <table className="file-table">
                      <thead>
                        <tr>
                          <th>file</th>
                          <th>name</th>
                          <th>verdict</th>
                          <th>reason</th>
                        </tr>
                      </thead>
                      <tbody>
                        {result.losses.map(([path, name, verdict, reason], i) => (
                          <tr key={i}>
                            <td>
                              <code>{path}</code>
                            </td>
                            <td>
                              <code>{name}</code>
                            </td>
                            <td>{verdict}</td>
                            <td>{reason}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                {result.markdown ? (
                  <>
                    <div className="download-row">
                      <button onClick={() => download('conversion-report.md', result.markdown, 'text/markdown')}>
                        <Download size={15} /> conversion-report.md
                      </button>
                    </div>
                    <Code code={result.markdown} language="plain" lineNumbers={false} />
                  </>
                ) : (
                  <p className="muted-copy">No report was written.</p>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </section>
  )
}

// What the run has done so far, one line per stage, oldest first. The same
// shape as the single-file lab's trail and the same CSS, because it is the
// same idea: a minutes-long run should read as a sequence somebody can follow,
// not as one line of text that keeps being replaced by a different one.
function Stages({ stages }: { stages: string[] }) {
  return (
    <ol className="trail live" aria-live="polite">
      {stages.map((label, index) => {
        const last = index === stages.length - 1
        return (
          <li key={index} className={last ? 'current' : 'past'}>
            <span className="trail-dot">
              {last ? <Loader2 size={11} className="spin" /> : <Check size={11} />}
            </span>
            <span>{label}</span>
          </li>
        )
      })}
    </ol>
  )
}

function Stat({ label, value, tone }: { label: string; value: string; tone: 'good' | 'warn' | 'bad' | 'idle' }) {
  return (
    <div className={`stat tone-${tone}`}>
      <small>{label}</small>
      <strong>{value}</strong>
    </div>
  )
}

function FileTable({ rows }: { rows: FileRow[] }) {
  return (
    <div className="table-scroll">
      <table className="file-table">
        <thead>
          <tr>
            <th>file</th>
            <th>status</th>
            <th>gates</th>
            <th>why</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.path}>
              <td>
                <code>{row.path}</code>
              </td>
              <td>
                <span className={`pill status-${row.status}`}>{row.status}</span>
              </td>
              <td>{row.gates_line}</td>
              <td className="why">
                {row.reason}
                {row.errors.length > 0 && <div className="row-error">{row.errors.join('; ')}</div>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
