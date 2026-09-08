import { Github, Linkedin } from 'lucide-react'
import { GITHUB, LINKEDIN } from '../links'
import Brand from './Brand'

export default function Footer() {
  return (
    <footer className="site-footer">
      <div className="footer-brand">
        <Brand size={16} />
        <strong>Selenium → Playwright</strong>
      </div>
      <p>Built by Varun Bhatt · Senior SDET · Python, LangGraph, TypeScript</p>
      <div className="footer-links">
        <a href={LINKEDIN} target="_blank" rel="noreferrer">
          <Linkedin size={15} /> LinkedIn
        </a>
        <a href={GITHUB} target="_blank" rel="noreferrer">
          <Github size={15} /> GitHub
        </a>
      </div>
    </footer>
  )
}
