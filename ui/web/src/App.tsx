import { useCallback, useEffect, useRef, useState } from 'react'
import Nav from './components/Nav'
import Hero from './components/Hero'
import Lab from './components/Lab'
import SuiteLab from './components/SuiteLab'
import Approach from './components/Approach'
import { getLimits, getSession } from './api'
import type { Limits, Session } from './types'

export default function App() {
  const [session, setSession] = useState<Session | null>(null)
  const [limits, setLimits] = useState<Limits | null>(null)
  const [boot, setBoot] = useState('')
  const labRef = useRef<HTMLElement>(null)
  const suiteRef = useRef<HTMLElement>(null)

  useEffect(() => {
    getSession()
      .then((s) => {
        setSession(s)
        setLimits(s.limits)
      })
      .catch((err: Error) => setBoot(err.message))
  }, [])

  // The budget line is the one place on the page whose whole job is to be
  // accurate, so it is re-read after every run rather than counted down here.
  const refreshLimits = useCallback(() => {
    getLimits().then(setLimits).catch(() => undefined)
  }, [])

  const scrollTo = (ref: React.RefObject<HTMLElement | null>) =>
    ref.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })

  return (
    <div className="site-shell">
      <Nav onOpenLab={() => scrollTo(labRef)} onOpenSuite={() => scrollTo(suiteRef)} />
      <main>
        <Hero limits={limits} onOpenLab={() => scrollTo(labRef)} onOpenSuite={() => scrollTo(suiteRef)} />
        {boot && (
          <div className="boot-error" role="alert">
            The page could not reach its own server: {boot}
          </div>
        )}
        <Lab ref={labRef} session={session} limits={limits} onSpent={refreshLimits} />
        <SuiteLab ref={suiteRef} limits={limits} onSpent={refreshLimits} />
        <Approach onOpenLab={() => scrollTo(labRef)} />
      </main>
    </div>
  )
}
