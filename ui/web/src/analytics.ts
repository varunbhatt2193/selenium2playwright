// Cloudflare Web Analytics, and only if a token was supplied at build time.
//
// The page had no analytics at all, which meant a visitor who read it and left
// was invisible: the only visitor record anywhere is `s2p_limits`, and that
// counts people who spent money, not people who came.
//
// Cloudflare's beacon is used rather than Google Analytics for one reason that
// matters here: it sets no cookies and identifies nobody, so the page needs no
// consent banner. A consent dialog is a poor first thing to show someone on a
// page whose pitch is "no signup, nothing to install".
//
// The token is a build-time variable, not a secret — it ends up in the HTML of
// every page load either way. Absent, this function does nothing at all, which
// is what `npm run dev` and every CI build want: local clicks and a Playwright
// run in GitHub Actions are not visitors.

const BEACON = 'https://static.cloudflareinsights.com/beacon.min.js'

export function startAnalytics(): void {
  const token = import.meta.env.VITE_CF_BEACON_TOKEN
  if (!token) return

  const script = document.createElement('script')
  script.defer = true
  script.src = BEACON
  // Cloudflare reads its configuration out of this attribute as JSON.
  script.dataset.cfBeacon = JSON.stringify({ token })
  document.head.appendChild(script)
}
