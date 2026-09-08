import { useMemo } from 'react'
import hljs from 'highlight.js/lib/core'
import typescript from 'highlight.js/lib/languages/typescript'
import diff from 'highlight.js/lib/languages/diff'

hljs.registerLanguage('typescript', typescript)
hljs.registerLanguage('diff', diff)

type Props = {
  code: string
  language: 'typescript' | 'diff' | 'plain'
  lineNumbers?: boolean
  ariaLabel?: string
}

// A read-only code block with line numbers. highlight.js emits spans with
// class names; the colours for them live in styles.css under .hljs-*.
export default function Code({ code, language, lineNumbers = true, ariaLabel }: Props) {
  const lines = useMemo(() => {
    const html =
      language === 'plain'
        ? escape(code)
        : hljs.highlight(code, { language, ignoreIllegals: true }).value
    // Splitting highlighted HTML on newlines keeps spans balanced only when
    // no token spans a line break, which is true for these two grammars in
    // practice (block comments are the exception and merely lose colour).
    return html.split('\n')
  }, [code, language])

  return (
    <pre className={`code ${lineNumbers ? 'numbered' : ''}`} aria-label={ariaLabel}>
      <code>
        {lines.map((line, index) => (
          <span className="line" key={index}>
            {lineNumbers && <span className="ln">{index + 1}</span>}
            <span className="lc" dangerouslySetInnerHTML={{ __html: line || ' ' }} />
          </span>
        ))}
      </code>
    </pre>
  )
}

function escape(text: string) {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}
