import { forwardRef, useEffect, useMemo, useRef, useState } from 'react'
import {
  Check,
  ChevronRight,
  Copy,
  Download,
  FileCode2,
  Loader2,
  Play,
  RotateCcw,
  ShieldCheck,
  ThumbsDown,
  ThumbsUp,
  UploadCloud,
  Wand2,
  X,
  Zap,
} from 'lucide-react'
import { convert, download, sendFeedback } from '../api'
import type { ConvertDone, Limits, Sample, Session } from '../types'
import Code from './Code'
import Ring from './Ring'

type Props = { session: Session | null; limits: Limits | null; onSpent: () => void }
type Tab = 'before' | 'after' | 'diff'
type Status = 'idle' | 'running' | 'done'

// What the agent does, in order, for the empty state. Same six steps the
// Streamlit sidebar listed, kept here so the empty right-hand pane teaches
// instead of waiting.
const STEPS: [string, string][] = [
  ['Intake', 'reads the file and classifies it: page object, spec, or something to refuse'],
  ['Recall', 'looks in long-term memory for conventions worth applying'],
  ['Convert', 'one model writes the Playwright version'],
  ['Validate', 'four deterministic gates on the written file: tsc, residue, ESLint, parity'],
  ['Critic', 'a second model reads the result and the gate findings'],
  ['Repeat', 'anything failed goes round again with those findings — at most three attempts'],
]

const MAX_BYTES = 256 * 1024
const BARE_NAME = /^(?!\.)[A-Za-z0-9._-]{1,255}$/

// The same checks playground.check_input makes, one hop earlier, so the button
// greys out with a reason instead of the request coming back 400.
function complaint(source: string, filename: string, companionName: string, companionText: string): string {
  if (!source.trim()) return 'Paste a TypeScript Selenium file, or pick a sample.'
  const size = new TextEncoder().encode(source).length
  if (size > MAX_BYTES) return `That is ${Math.floor(size / 1024)} KB. The demo takes files up to ${MAX_BYTES / 1024} KB.`
  if (filename && !BARE_NAME.test(filename)) return 'The file name must be a plain name like LoginPage.ts — no folders.'
  if (companionText.trim() && !companionName.trim()) return 'Give the companion a file name, so the converted file can import it.'
  if (companionName && !BARE_NAME.test(companionName)) return 'The companion name must be a plain name — no folders.'
  return ''
}

const Lab = forwardRef<HTMLElement, Props>(function Lab({ session, limits, onSpent }, ref) {
  const [source, setSource] = useState('')
  const [filename, setFilename] = useState('')
  const [companionName, setCompanionName] = useState('')
  const [companionText, setCompanionText] = useState('')
  const [showCompanion, setShowCompanion] = useState(false)
  const [status, setStatus] = useState<Status>('idle')
  const [trail, setTrail] = useState<string[]>([])
  const [result, setResult] = useState<ConvertDone | null>(null)
  const [error, setError] = useState('')
  const [tab, setTab] = useState<Tab>('before')
  const [refinement, setRefinement] = useState('')
  const [comment, setComment] = useState('')
  const [verdict, setVerdict] = useState('')
  const [copied, setCopied] = useState(false)
  const [showUploader, setShowUploader] = useState(false)
  const [dragging, setDragging] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const uploaderTarget = useRef<'source' | 'companion'>('source')
  const loaded = useRef(false)

  // The first sample, ready in the box the moment the page has one: a demo
  // that opens on an empty text area asks the visitor to do the work.
  useEffect(() => {
    if (session && session.samples.length && !loaded.current) {
      loaded.current = true
      pick(session.samples[0])
    }
  }, [session])

  const problem = useMemo(
    () => complaint(source, filename, companionName, companionText),
    [source, filename, companionName, companionText],
  )
  const outName = filename ? (filename.endsWith('.ts') ? filename : `${filename}.ts`) : 'pasted.ts'
  const card = result?.card ?? null

  function pick(sample: Sample) {
    setSource(sample.source)
    setFilename(sample.name)
    setCompanionName(sample.companion_name)
    setCompanionText(sample.companion_text)
    setShowCompanion(Boolean(sample.companion_text))
    clearResult()
  }

  function clearResult() {
    setResult(null)
    setTrail([])
    setError('')
    setVerdict('')
    setRefinement('')
    setTab('before')
    setStatus('idle')
  }

  function reset() {
    if (session?.samples.length) pick(session.samples[0])
    else {
      setSource('')
      setFilename('')
      setCompanionName('')
      setCompanionText('')
      clearResult()
    }
  }

  async function run(refine = '') {
    setStatus('running')
    setError('')
    setVerdict('')
    setTrail([])
    if (!refine) setResult(null)
    try {
      for await (const event of convert({
        source,
        filename,
        companion_name: companionName,
        companion_text: companionText,
        refinement: refine,
        thread_id: refine ? result?.thread_id : '',
      })) {
        if (event.kind === 'node') setTrail((t) => [...t, event.label])
        else if (event.kind === 'done') {
          setResult(event)
          setTab(event.card.code ? 'after' : 'before')
        } else if (event.kind === 'error') setError(event.message)
      }
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setStatus('done')
      setRefinement('')
      onSpent()
    }
  }

  function readFile(file?: File) {
    if (!file) return
    file.text().then((text) => {
      if (uploaderTarget.current === 'companion') {
        setCompanionName(file.name)
        setCompanionText(text.replace(/^﻿/, ''))
        setShowCompanion(true)
      } else {
        setSource(text.replace(/^﻿/, ''))
        setFilename(file.name)
        clearResult()
      }
      setShowUploader(false)
    })
  }

  function openUploader(target: 'source' | 'companion') {
    uploaderTarget.current = target
    setShowUploader(true)
  }

  async function copyCode() {
    if (!card?.code) return
    try {
      await navigator.clipboard.writeText(card.code)
      setCopied(true)
      setTimeout(() => setCopied(false), 1400)
    } catch {
      /* clipboard blocked: the download button still works */
    }
  }

  async function vote(score: number) {
    if (!result?.run_id) return
    const answer = await sendFeedback({
      run_id: result.run_id,
      score,
      comment,
      source_text: source,
      source_path: filename,
    })
    setVerdict(answer.detail)
  }

  const running = status === 'running'
  const budgetOut = Boolean(limits?.budget && limits.budget.remaining <= 0)

  return (
    <section className="lab-section" ref={ref} aria-labelledby="lab-title" id="lab">
      <div className="section-kicker">01 · Live conversion</div>
      <div className="section-heading-row">
        <div>
          <h2 id="lab-title">Watch one file convert, gate by gate.</h2>
          <p>
            Pick a sample from the real suite or paste your own. Every result below was compiled by a real
            TypeScript compiler before you saw it.
          </p>
        </div>
        <button className="secondary-button" onClick={() => openUploader('source')}>
          <UploadCloud size={16} /> Upload .ts
        </button>
      </div>

      {session && session.samples.length > 0 && (
        <div className="samples" role="list" aria-label="Sample files">
          {session.samples.map((sample) => (
            <button
              key={sample.name}
              role="listitem"
              className={`sample-chip ${sample.name === filename ? 'active' : ''}`}
              title={sample.blurb}
              onClick={() => pick(sample)}
              disabled={running}
            >
              <FileCode2 size={14} />
              {sample.name}
              {sample.companion_name && <span className="chip-note">+ companion</span>}
            </button>
          ))}
        </div>
      )}

      <div className="lab-card">
        <div className="lab-toolbar">
          <div className="file-chip">
            <FileCode2 size={16} />
            <input
              value={filename}
              onChange={(e) => setFilename(e.target.value)}
              placeholder="LoginPage.ts"
              aria-label="File name"
              spellCheck={false}
              disabled={running}
            />
            {status === 'done' && card && (
              <span className={`file-state ${card.passed ? 'ok' : ''}`}>
                <Check size={12} /> {card.status}
              </span>
            )}
            {running && (
              <span className="file-state">
                <Loader2 size={12} className="spin" /> converting
              </span>
            )}
          </div>
          <div className="toolbar-actions">
            <button onClick={reset} title="Back to the first sample" disabled={running}>
              <RotateCcw size={15} /> Reset
            </button>
            <button
              className="run-button"
              onClick={() => run()}
              disabled={running || Boolean(problem) || budgetOut}
              title={problem || (budgetOut ? limits?.line : 'Send it to the agent')}
            >
              {running ? <Loader2 size={14} className="spin" /> : <Play size={14} fill="currentColor" />}
              {running ? 'Converting…' : 'Run conversion'}
            </button>
          </div>
        </div>
        {problem && status !== 'running' && <div className="toolbar-hint">{problem}</div>}

        <div className="lab-grid">
          <div className="code-pane">
            <div className="code-tabs" role="tablist">
              <button className={tab === 'before' ? 'active' : ''} onClick={() => setTab('before')} role="tab">
                Selenium · TypeScript
              </button>
              <ChevronRight size={15} />
              <button
                className={tab === 'after' ? 'active' : ''}
                onClick={() => setTab('after')}
                role="tab"
                disabled={!card?.code}
              >
                Playwright · TypeScript
              </button>
              <button
                className={`tab-diff ${tab === 'diff' ? 'active' : ''}`}
                onClick={() => setTab('diff')}
                role="tab"
                disabled={!result?.diff}
              >
                Diff
              </button>
            </div>
            <div className="code-topline">
              <span>{tab === 'before' ? filename || 'pasted.ts' : tab === 'after' ? outName : `${filename || 'selenium.ts'} → ${outName}`}</span>
              <span>
                {tab === 'before' && <>editable · {source.split('\n').length} lines</>}
                {tab === 'after' && (
                  <>
                    <Zap size={12} /> converted
                  </>
                )}
                {tab === 'diff' && <>unified diff</>}
              </span>
            </div>

            {tab === 'before' && (
              <textarea
                className="editor"
                value={source}
                onChange={(e) => {
                  setSource(e.target.value)
                  if (result) clearResult()
                }}
                placeholder="import { By, until, WebDriver } from 'selenium-webdriver';"
                spellCheck={false}
                aria-label="Selenium TypeScript source"
                disabled={running}
              />
            )}
            {tab === 'after' && card && <Code code={card.code} language="typescript" ariaLabel="Converted Playwright code" />}
            {tab === 'diff' && result && <Code code={result.diff} language="diff" lineNumbers={false} ariaLabel="Diff" />}

            {tab === 'before' && (
              <div className="companion">
                <button className="companion-toggle" onClick={() => setShowCompanion((v) => !v)}>
                  <ChevronRight size={14} className={showCompanion ? 'rot' : ''} />
                  Companion file
                  {companionText.trim() && companionName ? <span className="chip-note">sending {companionName}</span> : <span className="chip-note muted">optional</span>}
                </button>
                {showCompanion && (
                  <div className="companion-body">
                    <p>
                      If this file imports another one, give the compiler the <strong>already-converted</strong>{' '}
                      Playwright version of it — the same thing the suite run does in wave two.
                    </p>
                    <div className="companion-row">
                      <input
                        value={companionName}
                        onChange={(e) => setCompanionName(e.target.value)}
                        placeholder="LoginPage.ts"
                        aria-label="Companion file name"
                        spellCheck={false}
                      />
                      <button className="mini-button" onClick={() => openUploader('companion')}>
                        <UploadCloud size={13} /> Upload
                      </button>
                    </div>
                    <textarea
                      className="editor small"
                      value={companionText}
                      onChange={(e) => setCompanionText(e.target.value)}
                      placeholder="import { Page } from '@playwright/test';"
                      spellCheck={false}
                      aria-label="Companion Playwright source"
                    />
                  </div>
                )}
              </div>
            )}
          </div>

          <aside className="coverage-pane" aria-label="Scorecard">
            {status === 'idle' && !card && (
              <>
                <div className="coverage-heading">
                  <span>What happens when you press Run</span>
                </div>
                <ol className="steps-list">
                  {STEPS.map(([name, text]) => (
                    <li key={name}>
                      <strong>{name}</strong>
                      <span>{text}</span>
                    </li>
                  ))}
                </ol>
                <div className="review-note muted">
                  <ShieldCheck size={16} />
                  <span>
                    <strong>needs-review is a real outcome</strong>
                    <small>It means the code compiles and something still needs your eyes. The TODOs say what.</small>
                  </span>
                </div>
              </>
            )}

            {running && (
              <>
                <div className="coverage-heading">
                  <span>Agent trace</span>
                  <span className="coverage-badge live">
                    <Loader2 size={12} className="spin" /> running
                  </span>
                </div>
                <Trail trail={trail} live />
              </>
            )}

            {!running && error && (
              <div className="error-box" role="alert">
                <X size={16} />
                <span>{error}</span>
              </div>
            )}

            {!running && card && (
              <>
                <div className="coverage-heading">
                  <span>Scorecard</span>
                  <span className={`coverage-badge status-${card.status}`}>
                    {card.passed ? <Check size={12} /> : null} {card.status}
                  </span>
                </div>
                <div className="coverage-score-row">
                  <Ring
                    value={card.gates.filter(([, ok]) => ok).length}
                    max={card.gates.length || 1}
                    label={card.gates.length ? `${card.gates.filter(([, ok]) => ok).length}/${card.gates.length}` : '—'}
                    tone={card.passed ? 'good' : card.status === 'needs-review' ? 'warn' : card.gates.length ? 'bad' : 'idle'}
                  />
                  <div className="score-copy">
                    <strong>
                      {card.gates.length ? `${card.gates_line} passed` : 'No gates ran'} · {card.attempts || 0} attempt
                      {card.attempts === 1 ? '' : 's'}
                    </strong>
                    <p>{card.reason}</p>
                  </div>
                </div>

                {card.gates.length > 0 && (
                  <div className="metric-list">
                    {card.gates.map(([gate, ok]) => (
                      <div className="metric" key={gate}>
                        <div>
                          <span>{gate}</span>
                          <small>{GATE_NOTE[gate] ?? 'deterministic gate'}</small>
                        </div>
                        <div className={`verdict ${ok ? 'ok' : 'fail'}`}>{ok ? 'PASS' : 'FAIL'}</div>
                      </div>
                    ))}
                    <div className="metric">
                      <div>
                        <span>critic</span>
                        <small>a second model, reading the gate findings</small>
                      </div>
                      <div className={`verdict ${card.critic === 'pass' ? 'ok' : card.critic === 'unavailable' ? 'idle' : 'fail'}`}>
                        {card.critic.toUpperCase()}
                      </div>
                    </div>
                  </div>
                )}

                {Object.keys(card.models).length > 0 && (
                  <div className="models-line">
                    {Object.entries(card.models).map(([role, name]) => (
                      <span key={role}>
                        <small>{role}</small> <code>{name}</code>
                      </span>
                    ))}
                  </div>
                )}

                {result && result.trail.length > 0 && (
                  <details className="trail-details">
                    <summary>What it did — {result.trail.length} steps</summary>
                    <Trail trail={result.trail} />
                  </details>
                )}

                <div className={`review-note ${card.todos.length ? 'warn' : ''}`}>
                  <ShieldCheck size={16} />
                  <span>
                    <strong>
                      {card.todos.length ? `${card.todos.length} TODO(review) item${card.todos.length === 1 ? '' : 's'}` : 'No TODO(review) items'}
                    </strong>
                    <small>
                      {card.todos.length ? 'Things the agent would not guess at. Each one is in the code too.' : 'Nothing left for a person to decide.'}
                    </small>
                  </span>
                </div>
                {card.todos.length > 0 && (
                  <ul className="todo-list">
                    {card.todos.map((todo, i) => (
                      <li key={i}>{todo}</li>
                    ))}
                  </ul>
                )}
                {card.notes.length > 0 && (
                  <details className="trail-details">
                    <summary>Notes from the conversion — {card.notes.length}</summary>
                    <ul className="todo-list plain">
                      {card.notes.map((note, i) => (
                        <li key={i}>{note}</li>
                      ))}
                    </ul>
                  </details>
                )}
                {card.errors.length > 0 && (
                  <ul className="todo-list errors">
                    {card.errors.map((e, i) => (
                      <li key={i}>{e}</li>
                    ))}
                  </ul>
                )}

                {card.code && (
                  <>
                    <div className="download-row">
                      <button onClick={() => download(result!.download_name, card.code, 'text/typescript')}>
                        <Download size={15} /> Download {result!.download_name}
                      </button>
                      <button onClick={copyCode}>
                        {copied ? <Check size={15} /> : <Copy size={15} />} {copied ? 'Copied' : 'Copy'}
                      </button>
                    </div>

                    <form
                      className="refine"
                      onSubmit={(e) => {
                        e.preventDefault()
                        if (refinement.trim()) run(refinement.trim())
                      }}
                    >
                      <label htmlFor="refine-input">
                        <Wand2 size={14} /> Ask for a change
                        <small>a second turn on the same thread — the agent still has this draft</small>
                      </label>
                      <div className="refine-row">
                        <input
                          id="refine-input"
                          value={refinement}
                          onChange={(e) => setRefinement(e.target.value)}
                          placeholder="Use getByRole for the buttons."
                          disabled={running}
                        />
                        <button type="submit" disabled={!refinement.trim() || running || budgetOut}>
                          Refine
                        </button>
                      </div>
                    </form>
                  </>
                )}

                <div className="verdict-row">
                  <span>Was this a good conversion?</span>
                  <input
                    value={comment}
                    onChange={(e) => setComment(e.target.value)}
                    placeholder="Anything to add? (optional)"
                    aria-label="Feedback comment"
                  />
                  <div className="thumbs">
                    <button onClick={() => vote(1)} disabled={!result?.run_id} aria-label="Good conversion">
                      <ThumbsUp size={15} />
                    </button>
                    <button onClick={() => vote(0)} disabled={!result?.run_id} aria-label="Bad conversion">
                      <ThumbsDown size={15} />
                    </button>
                  </div>
                </div>
                {verdict && <div className="verdict-note">{verdict}</div>}
              </>
            )}
          </aside>
        </div>
      </div>

      {showUploader && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setShowUploader(false)}>
          <div
            className="upload-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="upload-title"
            onMouseDown={(e) => e.stopPropagation()}
          >
            <button className="modal-close" onClick={() => setShowUploader(false)} aria-label="Close upload dialog">
              <X size={18} />
            </button>
            <span className="modal-icon">
              <UploadCloud size={22} />
            </span>
            <h3 id="upload-title">
              {uploaderTarget.current === 'companion' ? 'Add the converted companion' : 'Add a Selenium file'}
            </h3>
            <p>
              {uploaderTarget.current === 'companion'
                ? 'The already-converted Playwright file this one imports.'
                : 'A TypeScript Selenium page object or spec. It lands in the editor, where you can still trim it.'}
            </p>
            <div
              className={`dropzone ${dragging ? 'dragging' : ''}`}
              onDragOver={(e) => {
                e.preventDefault()
                setDragging(true)
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => {
                e.preventDefault()
                setDragging(false)
                readFile(e.dataTransfer.files?.[0])
              }}
              onClick={() => fileRef.current?.click()}
            >
              <input
                ref={fileRef}
                type="file"
                accept=".ts,.tsx,.js,.mjs,.cjs,text/typescript,text/javascript"
                onChange={(e) => readFile(e.target.files?.[0])}
                hidden
              />
              <FileCode2 size={27} />
              <strong>Drop your file here</strong>
              <span>or click to browse · up to 256 KB</span>
            </div>
            <div className="privacy-note">
              <ShieldCheck size={14} /> Sent as text with the request, converted, and not kept.
            </div>
          </div>
        </div>
      )}
    </section>
  )
})

export default Lab

const GATE_NOTE: Record<string, string> = {
  compile: 'the written file, through the real tsc',
  residue: 'no selenium-webdriver left behind',
  lint: 'ESLint, Playwright rules included',
  parity: 'no test quietly disappeared',
}

function Trail({ trail, live = false }: { trail: string[]; live?: boolean }) {
  return (
    <ol className={`trail ${live ? 'live' : ''}`} aria-live={live ? 'polite' : undefined}>
      {trail.map((label, index) => {
        const last = index === trail.length - 1
        return (
          <li key={index} className={live && last ? 'current' : 'past'}>
            <span className="trail-dot">{live && last ? <Loader2 size={11} className="spin" /> : <Check size={11} />}</span>
            <span>{label}</span>
          </li>
        )
      })}
      {live && trail.length === 0 && (
        <li className="current">
          <span className="trail-dot">
            <Loader2 size={11} className="spin" />
          </span>
          <span>Sending the file to the agent…</span>
        </li>
      )}
    </ol>
  )
}
