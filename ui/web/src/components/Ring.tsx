type Props = { value: number; max: number; label: string; tone?: 'good' | 'warn' | 'bad' | 'idle'; size?: number }

// A progress ring, drawn with a conic gradient so it needs no library.
export default function Ring({ value, max, label, tone = 'good', size = 92 }: Props) {
  const pct = max > 0 ? Math.max(0, Math.min(100, Math.round((value / max) * 100))) : 0
  return (
    <div
      className={`ring tone-${tone}`}
      style={{ '--score': pct, width: size, height: size } as React.CSSProperties}
      role="img"
      aria-label={`${label}`}
    >
      <span>{label}</span>
    </div>
  )
}
