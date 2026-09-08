// The shapes ui/server.py sends. Every one of them is a dataclass from
// playground.py turned into JSON, plus the odd computed property.

export type Sample = {
  name: string
  blurb: string
  source: string
  companion_name: string
  companion_text: string
}

export type Limits = {
  error?: string
  line: string
  visitor_line: string
  budget?: { used: number; remaining: number; limit: number }
  budget_usd_per_day?: number
  per_visitor?: { daily: number; burst: number; window_s: number }
}

export type Session = {
  visitor: string
  backend: string
  key_present: boolean
  samples: Sample[]
  limits: Limits
}

export type Gate = [string, boolean]

export type Scorecard = {
  status: 'passed' | 'needs-review' | 'refused' | 'unavailable' | string
  attempts: number
  reason: string
  gates: Gate[]
  critic: string
  code: string
  todos: string[]
  notes: string[]
  models: Record<string, string>
  errors: string[]
  passed: boolean
  gates_line: string
}

export type ConvertDone = {
  thread_id: string
  run_id: string
  trail: string[]
  card: Scorecard
  diff: string
  download_name: string
}

export type ConvertEvent =
  | { kind: 'run'; run_id: string; thread_id: string }
  | { kind: 'node'; node: string; label: string }
  | ({ kind: 'done' } & ConvertDone)
  | { kind: 'error'; message: string }

export type SuitePlan = {
  root: string
  waves: string[][]
  convert: string[]
  copied: string[]
  skipped: [string, string][]
  notes: string[]
  billable: number
  files: number
  line: string
}

export type PlanResponse = {
  tree: Record<string, string>
  plan: SuitePlan
  unaffordable: string
}

export type FileRow = {
  path: string
  wave: number
  status: 'passed' | 'needs-review' | 'refused' | 'failed' | string
  attempts: number
  reason: string
  gates: Gate[]
  critic: string
  todos: string[]
  seconds: number
  written: string
  errors: string[]
  gates_line: string
}

export type SuiteResult = {
  rows: FileRow[]
  elapsed: number
  compiles: boolean
  tree_files: number
  tree_findings: string[]
  tree_error: string
  kept: number
  renamed: number
  removed: number
  unexplained: number
  losses: [string, string, string, string][]
  todos: [string, string[]][]
  notes: string[]
  report_path: string
  markdown: string
  assembled: boolean
  tree: Record<string, string>
  totals: Record<string, number>
  passed: boolean
  headline: string
}

export type SuiteEvent =
  | { kind: 'start'; files: number; waves: number }
  | { kind: 'node'; node: string; label: string }
  | { kind: 'file'; landed: number; of: number; row: FileRow }
  | { kind: 'done'; result: SuiteResult }
  | { kind: 'error'; message: string }
