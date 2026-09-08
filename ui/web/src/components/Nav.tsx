import { FileCode2, FlaskConical, FolderArchive, Github, Home, Linkedin, Workflow } from 'lucide-react'
import { GITHUB, LINKEDIN } from '../links'
import { Link, type Page } from '../router'
import Brand from './Brand'

type Props = { page: Page | null }

const ITEMS: [Page, string, typeof Home][] = [
  ['/', 'Home', Home],
  ['/convert', 'Single file', FileCode2],
  ['/suite', 'Whole suite', FolderArchive],
  ['/how-it-works', 'How it works', Workflow],
  ['/evaluation', 'Evaluation', FlaskConical],
]

export default function Nav({ page }: Props) {
  return (
    <header className="nav-wrap">
      <nav className="nav" aria-label="Primary navigation">
        <Link to="/" className="brand" aria-label="Home">
          <Brand />
          <span>
            Selenium <span className="brand-arrow">→</span> Playwright
          </span>
        </Link>
        <div className="nav-meta">
          {ITEMS.map(([to, label, Icon]) => (
            <Link
              key={to}
              to={to}
              className={`nav-link ${page === to ? 'active' : ''}`}
              aria-current={page === to ? 'page' : undefined}
            >
              <Icon size={15} /> <span>{label}</span>
            </Link>
          ))}
          <span className="nav-sep" aria-hidden="true" />
          <a className="nav-link nav-icon" href={GITHUB} target="_blank" rel="noreferrer" aria-label="Source on GitHub">
            <Github size={16} /> <span>GitHub</span>
          </a>
          <a className="nav-link nav-icon" href={LINKEDIN} target="_blank" rel="noreferrer" aria-label="Varun Bhatt on LinkedIn">
            <Linkedin size={16} /> <span>LinkedIn</span>
          </a>
        </div>
      </nav>
    </header>
  )
}
