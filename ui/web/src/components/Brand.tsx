// The mark: two chevrons, Selenium amber handing off to Playwright green.
export default function Brand({ size = 18 }: { size?: number }) {
  return (
    <span className="brand-mark" style={{ width: size + 12, height: size + 12 }}>
      <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden="true">
        <path d="M14 18h12l8 14-8 14H14l8-14z" fill="#f2b84b" />
        <path d="M34 18h14l-8 14 8 14H34l8-14z" fill="#45d483" />
      </svg>
    </span>
  )
}
