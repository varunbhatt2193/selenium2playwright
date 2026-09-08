import { useCallback, useEffect, useState } from 'react'
import Nav from './components/Nav'
import Footer from './components/Footer'
import Home from './components/Home'
import Lab from './components/Lab'
import SuiteLab from './components/SuiteLab'
import HowItWorks from './components/HowItWorks'
import Evaluation from './components/Evaluation'
import { getLimits, getSession } from './api'
import { currentPage, Link, PAGES, usePathname } from './router'
import type { Limits, Session } from './types'

export default function App() {
  const [session, setSession] = useState<Session | null>(null)
  const [limits, setLimits] = useState<Limits | null>(null)
  const [boot, setBoot] = useState('')
  const pathname = usePathname()
  const page = currentPage(pathname)

  useEffect(() => {
    getSession()
      .then((s) => {
        setSession(s)
        setLimits(s.limits)
      })
      .catch((err: Error) => setBoot(err.message))
  }, [])

  // A new page starts at its top, with its own tab title.
  useEffect(() => {
    window.scrollTo({ top: 0 })
    document.title = page ? PAGES[page] : 'Page not found · Selenium → Playwright'
  }, [page])

  // The budget line is the one place on the page whose whole job is to be
  // accurate, so it is re-read after every run rather than counted down here.
  const refreshLimits = useCallback(() => {
    getLimits().then(setLimits).catch(() => undefined)
  }, [])

  return (
    <div className="site-shell">
      <Nav page={page} />
      <main>
        {boot && page !== '/' && (
          <div className="boot-error" role="alert">
            The page could not reach its own server: {boot}
          </div>
        )}
        {page === '/' && <Home />}
        {page === '/convert' && <Lab session={session} limits={limits} onSpent={refreshLimits} />}
        {page === '/suite' && <SuiteLab limits={limits} onSpent={refreshLimits} />}
        {page === '/how-it-works' && <HowItWorks />}
        {page === '/evaluation' && <Evaluation />}
        {page === null && (
          <section className="not-found">
            <h1>There is no page at {pathname}</h1>
            <p>
              <Link to="/">Back to the front page</Link>
            </p>
          </section>
        )}
        <Footer />
      </main>
    </div>
  )
}
