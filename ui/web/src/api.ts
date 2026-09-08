// Talking to ui/server.py. Plain fetch; the only thing worth a comment is how
// a stream is read.
import type { ConvertEvent, Limits, PlanResponse, Session, SuiteEvent } from './types'

const VISITOR_KEY = 's2p-visitor'

// One visitor id per browser tab, the way the Streamlit page had one per
// session: the per-visitor limits are meant to count people, and every visitor
// shares this server's address. sessionStorage rather than localStorage so a
// new tab is a new visitor — it is not an identity and does not try to be one.
export function visitor(): string {
  try {
    return sessionStorage.getItem(VISITOR_KEY) || ''
  } catch {
    return ''
  }
}

function remember(id: string) {
  try {
    sessionStorage.setItem(VISITOR_KEY, id)
  } catch {
    /* private mode: a fresh visitor per request is fine */
  }
}

function headers(extra: Record<string, string> = {}): Record<string, string> {
  const id = visitor()
  return id ? { 'X-S2P-Visitor': id, ...extra } : extra
}

async function detail(response: Response): Promise<string> {
  try {
    const body = await response.json()
    if (body && typeof body.detail === 'string') return body.detail
    return JSON.stringify(body)
  } catch {
    return `${response.status} ${response.statusText}`
  }
}

export async function getSession(): Promise<Session> {
  const response = await fetch('/api/session', { headers: headers() })
  if (!response.ok) throw new Error(await detail(response))
  const session = (await response.json()) as Session
  remember(session.visitor)
  return session
}

export async function getLimits(): Promise<Limits> {
  const response = await fetch('/api/limits', { headers: headers() })
  if (!response.ok) throw new Error(await detail(response))
  return (await response.json()) as Limits
}

// Server-sent events over fetch. EventSource only does GET, and a conversion
// is a POST with a body, so this reads the response as a stream and splits it
// on the blank line that ends each event. Every event is one JSON object.
async function* events<T>(url: string, body: BodyInit, extra: Record<string, string> = {}): AsyncGenerator<T> {
  const response = await fetch(url, { method: 'POST', body, headers: headers(extra) })
  if (!response.ok) throw new Error(await detail(response))
  if (!response.body) throw new Error('The server sent no stream.')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let cut: number
    while ((cut = buffer.indexOf('\n\n')) >= 0) {
      const chunk = buffer.slice(0, cut)
      buffer = buffer.slice(cut + 2)
      for (const line of chunk.split('\n')) {
        if (line.startsWith('data: ')) yield JSON.parse(line.slice(6)) as T
      }
    }
  }
}

export type ConvertInput = {
  source: string
  filename: string
  companion_name: string
  companion_text: string
  refinement?: string
  thread_id?: string
}

export function convert(input: ConvertInput): AsyncGenerator<ConvertEvent> {
  return events<ConvertEvent>('/api/convert', JSON.stringify(input), { 'Content-Type': 'application/json' })
}

export async function sendFeedback(input: {
  run_id: string
  score: number
  comment: string
  source_text: string
  source_path: string
}): Promise<{ stored?: boolean; queued?: boolean; detail: string }> {
  const response = await fetch('/api/feedback', {
    method: 'POST',
    headers: headers({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(input),
  })
  if (!response.ok) return { detail: await detail(response) }
  return response.json()
}

export async function planSuite(files: File[], only: string): Promise<PlanResponse> {
  const form = new FormData()
  for (const file of files) form.append('files', file, file.name)
  form.append('only', only)
  const response = await fetch('/api/suite/plan', { method: 'POST', body: form, headers: headers() })
  if (!response.ok) throw new Error(await detail(response))
  return response.json()
}

// The twelve-file sample suite, already on the server, as a planned tree — the
// one-click way to watch a whole folder convert without owning a Selenium suite.
export async function sampleSuite(only: string): Promise<PlanResponse> {
  const response = await fetch(`/api/suite/sample?only=${encodeURIComponent(only)}`, { headers: headers() })
  if (!response.ok) throw new Error(await detail(response))
  return response.json()
}

export type SuiteInput = {
  tree: Record<string, string>
  only: string
  parallel: number
  attempts: number
  model: string
}

export function convertSuite(input: SuiteInput): AsyncGenerator<SuiteEvent> {
  return events<SuiteEvent>('/api/suite/convert', JSON.stringify(input), { 'Content-Type': 'application/json' })
}

export async function suiteZip(tree: Record<string, string>, markdown: string): Promise<Blob> {
  const response = await fetch('/api/suite/zip', {
    method: 'POST',
    headers: headers({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ tree, markdown }),
  })
  if (!response.ok) throw new Error(await detail(response))
  return response.blob()
}

export function download(filename: string, content: Blob | string, type = 'text/plain') {
  const blob = content instanceof Blob ? content : new Blob([content], { type })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}
