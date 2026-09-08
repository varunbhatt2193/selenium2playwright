// The technology list, in one place: the strip on the front page reads the
// short form, the How-it-works page reads the groups. Vendors are named,
// models are not — the provider is one setting (S2P_MODEL) and changes.

export const STACK_STRIP = [
  'Python',
  'LangGraph',
  'LangChain',
  'LangSmith',
  'OpenAI · Anthropic',
  'LLM-as-judge',
  'LangSmith evals',
  'openevals',
  'TypeScript',
  'React',
  'FastAPI',
  'Docker',
  'Fly.io',
  'Postgres',
  'Redis',
]

export type StackGroup = { title: string; blurb: string; items: [string, string][] }

export const STACK: StackGroup[] = [
  {
    title: 'The agent',
    blurb: 'A graph of small steps, not one big prompt.',
    items: [
      ['Python 3.12', 'the whole agent, CLI and evaluation harness'],
      ['LangGraph', 'the state machine: nodes, routing, the three-attempt loop, a suite as fan-out'],
      ['LangChain', 'one interface to the model; OpenAI or Anthropic behind one setting'],
      ['LangSmith', 'every run traced; evaluations and visitor feedback land as datasets'],
      ['openevals', 'the LLM-as-judge scorers used alongside the deterministic ones'],
    ],
  },
  {
    title: 'Verification',
    blurb: 'The same toolchain a CI pipeline would run, on every draft.',
    items: [
      ['TypeScript compiler', 'the converted file, then the whole converted folder as one project'],
      ['ESLint + Playwright plugin', 'the rules a Playwright team would enforce anyway'],
      ['Residue scan', 'a sandbox with no selenium-webdriver installed, so leftovers fail to compile'],
      ['Parity check', 'a parse-only AST pass: no public method or test quietly disappears'],
    ],
  },
  {
    title: 'The web layer',
    blurb: 'This page, and the small server behind it.',
    items: [
      ['React 19 + Vite', 'the page you are reading, TypeScript throughout, no UI framework'],
      ['FastAPI', 'a thin server: validates the request, calls the deployment, never holds the model key in the browser'],
      ['Server-sent events', 'the agent narrates each step to the page as it happens'],
    ],
  },
  {
    title: 'Infrastructure',
    blurb: 'Self-hosted, metered, with a dollar budget.',
    items: [
      ['Docker on Fly.io', 'the agent image carries Node and the pinned toolchain, so it can compile its own output'],
      ['Postgres + pgvector', 'run checkpoints and the long-term memory of conventions users teach it'],
      ['Redis', 'the run queue and the atomic counters behind per-visitor limits and the daily budget'],
      ['GitHub Actions', 'unit tests, the web build, execution evals in a real browser, CodeQL — no model spend'],
    ],
  },
]
