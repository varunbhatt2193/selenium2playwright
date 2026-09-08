import { ArrowRight, FileCode2, FolderArchive, Github, Linkedin, PlayCircle } from 'lucide-react'
import { STACK_STRIP } from '../stack'
import { GITHUB, LINKEDIN } from '../links'
import { Link } from '../router'
import Code from './Code'

// The demo video slot. Paste a YouTube or Loom share link, or the URL of an
// .mp4 (a file in ui/web/public is served from "/"). Empty renders nothing.
const DEMO_VIDEO = ''

// The whole explanation, as code: the same login step before and after.
const BEFORE = `await driver.findElement(By.id('email')).sendKeys('me@example.com');
await driver.findElement(By.css('button[type=submit]')).click();
await driver.wait(until.urlContains('/home'), 5000);`

const AFTER = `await page.locator('#email').fill('me@example.com');
await page.locator('button[type=submit]').click();
await expect(page).toHaveURL(/\\/home/);`

export default function Home() {
  return (
    <section className="home" aria-labelledby="home-title">
      <div className="home-noise" />
      <h1 id="home-title">
        Selenium in.
        <br />
        <em>Playwright out.</em>
      </h1>
      <p className="home-lede">
        Paste a TypeScript Selenium test. An AI agent rewrites it in Playwright and checks that the result compiles
        before you see it.
      </p>

      <div className="home-example" aria-label="Example conversion">
        <div className="example-pane">
          <span className="example-label">You give it Selenium</span>
          <Code code={BEFORE} language="typescript" lineNumbers={false} ariaLabel="Selenium example" />
        </div>
        <span className="example-arrow" aria-hidden="true">
          <ArrowRight size={22} />
        </span>
        <div className="example-pane">
          <span className="example-label after">You get back Playwright</span>
          <Code code={AFTER} language="typescript" lineNumbers={false} ariaLabel="Playwright example" />
        </div>
      </div>

      <div className="home-cards">
        <Link to="/convert" className="home-card">
          <span className="card-icon">
            <FileCode2 size={22} />
          </span>
          <strong>Convert one file</strong>
          <span>Paste or upload a Selenium file and watch it convert, check by check.</span>
          <em>
            Open <ArrowRight size={15} />
          </em>
        </Link>
        <Link to="/suite" className="home-card">
          <span className="card-icon">
            <FolderArchive size={22} />
          </span>
          <strong>Convert a whole suite</strong>
          <span>Drop a zip of your Selenium folder and get a Playwright folder back.</span>
          <em>
            Open <ArrowRight size={15} />
          </em>
        </Link>
      </div>

      <DemoVideo url={DEMO_VIDEO} />

      <div className="stack-strip" aria-label="Technologies used">
        <span className="example-label">Built with</span>
        <ul>
          {STACK_STRIP.map((name) => (
            <li key={name}>{name}</li>
          ))}
        </ul>
        <Link to="/how-it-works" className="stack-more">
          How it is built <ArrowRight size={15} />
        </Link>
      </div>

      <div className="home-links">
        <a href={LINKEDIN} target="_blank" rel="noreferrer">
          <Linkedin size={16} /> Varun Bhatt on LinkedIn
        </a>
        <a href={GITHUB} target="_blank" rel="noreferrer">
          <Github size={16} /> Source on GitHub
        </a>
      </div>
    </section>
  )
}

function DemoVideo({ url }: { url: string }) {
  if (!url) return null
  const embed = embedUrl(url)
  return (
    <div className="demo-video">
      <div className="example-label">
        <PlayCircle size={14} /> Two-minute demo
      </div>
      {embed ? (
        <iframe src={embed} title="Demo video" allow="fullscreen; picture-in-picture" allowFullScreen loading="lazy" />
      ) : (
        <video src={url} controls playsInline preload="metadata" />
      )}
    </div>
  )
}

// A share link for YouTube or Loom becomes the iframe form of itself; anything
// else is treated as a video file.
function embedUrl(url: string): string {
  const yt = url.match(/(?:youtu\.be\/|youtube\.com\/(?:watch\?v=|embed\/|shorts\/))([\w-]{11})/)
  if (yt) return `https://www.youtube-nocookie.com/embed/${yt[1]}`
  const loom = url.match(/loom\.com\/(?:share|embed)\/([a-f0-9]{32})/)
  if (loom) return `https://www.loom.com/embed/${loom[1]}`
  return ''
}
