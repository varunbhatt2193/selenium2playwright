import { useEffect, useState, type AnchorHTMLAttributes, type MouseEvent } from 'react'

// Five pages, one HTML file. `ui/server.py` answers every unknown path with
// `index.html`, so a real URL works on first load and on refresh; the page
// then reads `location.pathname` and draws the right one. No router library:
// a hook for the path, a function to change it, and a Link that uses both.

export const PAGES = {
  '/': 'Selenium → Playwright',
  '/convert': 'Convert one file · Selenium → Playwright',
  '/suite': 'Convert a whole suite · Selenium → Playwright',
  '/how-it-works': 'How it is built · Selenium → Playwright',
  '/evaluation': 'Evaluation · Selenium → Playwright',
} as const

export type Page = keyof typeof PAGES

export function currentPage(pathname = window.location.pathname): Page | null {
  const path = pathname.length > 1 ? pathname.replace(/\/+$/, '') : pathname
  return path in PAGES ? (path as Page) : null
}

export function usePathname(): string {
  const [path, setPath] = useState(window.location.pathname)
  useEffect(() => {
    const onChange = () => setPath(window.location.pathname)
    window.addEventListener('popstate', onChange)
    return () => window.removeEventListener('popstate', onChange)
  }, [])
  return path
}

export function navigate(to: string) {
  if (window.location.pathname !== to) window.history.pushState(null, '', to)
  // The browser only fires popstate for its own back/forward; fire it for ours
  // too so every usePathname() on the page hears the change.
  window.dispatchEvent(new PopStateEvent('popstate'))
}

type LinkProps = AnchorHTMLAttributes<HTMLAnchorElement> & { to: Page }

// A real anchor — middle-click, cmd-click and "copy link" all work — that
// stays on the page for a plain left click.
export function Link({ to, onClick, ...rest }: LinkProps) {
  function handle(event: MouseEvent<HTMLAnchorElement>) {
    onClick?.(event)
    if (event.defaultPrevented || event.button !== 0) return
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
    event.preventDefault()
    navigate(to)
  }
  return <a href={to} onClick={handle} {...rest} />
}
