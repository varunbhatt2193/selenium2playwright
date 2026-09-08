import { ArrowRight, Github, Radio } from 'lucide-react'
import Brand from './Brand'

type Props = { onOpenLab: () => void; onOpenSuite: () => void }

export default function Nav({ onOpenLab, onOpenSuite }: Props) {
  return (
    <header className="nav-wrap">
      <nav className="nav" aria-label="Primary navigation">
        <button className="brand" onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })} aria-label="Back to top">
          <Brand />
          <span>
            Selenium <span className="brand-arrow">→</span> Playwright
          </span>
        </button>
        <div className="nav-meta">
          <span className="live-pill">
            <Radio size={12} /> Live agent · metered
          </span>
          <button className="nav-link" onClick={onOpenSuite}>
            Whole suite
          </button>
          <a className="nav-link" href="#approach">
            How it works
          </a>
          <a
            className="nav-link nav-icon"
            href="https://github.com/varunbhatt2193/selenium2playwright"
            target="_blank"
            rel="noreferrer"
            aria-label="Source on GitHub"
          >
            <Github size={16} /> <span>Source</span>
          </a>
          <button className="nav-cta" onClick={onOpenLab}>
            Open lab <ArrowRight size={15} />
          </button>
        </div>
      </nav>
    </header>
  )
}
