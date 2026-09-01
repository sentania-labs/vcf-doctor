import { useMemo, useState, type ReactNode } from 'react'
import { Check, Copy, Eye, ShieldAlert } from 'lucide-react'
import { Badge } from '@/components/ui'

type Block =
  | { kind: 'heading'; level: number; text: string }
  | { kind: 'para'; text: string }
  | { kind: 'code'; lang: string; code: string; modifies: boolean }
  | { kind: 'ul'; items: string[] }
  | { kind: 'ol'; items: string[] }

// Small markdown parser: headings, paragraphs, fenced code, lists, inline code/bold/italic.
export function parseMarkdown(src: string): Block[] {
  const lines = src.replace(/\r\n/g, '\n').split('\n')
  const blocks: Block[] = []
  let lastHeading = ''
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    if (line.trim() === '') { i++; continue }
    const fence = line.match(/^\s*```\s*([\w+-]*)\s*$/)
    if (fence) {
      const lang = fence[1] ?? ''
      const buf: string[] = []
      i++
      while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) { buf.push(lines[i]); i++ }
      i++ // closing fence (or EOF while streaming)
      blocks.push({ kind: 'code', lang, code: buf.join('\n'), modifies: /MODIF/i.test(lastHeading) })
      continue
    }
    const h = line.match(/^(#{1,6})\s+(.*)$/)
    if (h) { lastHeading = h[2]; blocks.push({ kind: 'heading', level: h[1].length, text: h[2] }); i++; continue }
    if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = []
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) { items.push(lines[i].replace(/^\s*[-*]\s+/, '')); i++ }
      blocks.push({ kind: 'ul', items }); continue
    }
    if (/^\s*\d+[.)]\s+/.test(line)) {
      const items: string[] = []
      while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) { items.push(lines[i].replace(/^\s*\d+[.)]\s+/, '')); i++ }
      blocks.push({ kind: 'ol', items }); continue
    }
    const buf: string[] = []
    while (i < lines.length && lines[i].trim() !== '' && !/^\s*```/.test(lines[i]) && !/^#{1,6}\s/.test(lines[i]) && !/^\s*[-*]\s+/.test(lines[i]) && !/^\s*\d+[.)]\s+/.test(lines[i])) { buf.push(lines[i]); i++ }
    blocks.push({ kind: 'para', text: buf.join(' ') })
  }
  return blocks
}

function renderInline(text: string): ReactNode[] {
  const out: ReactNode[] = []
  const re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*]+\*)/g
  let last = 0
  let m: RegExpExecArray | null
  let k = 0
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index))
    if (m[1]) out.push(<code key={k++} className="inline">{m[1].slice(1, -1)}</code>)
    else if (m[2]) out.push(<strong key={k++}>{m[2].slice(2, -2)}</strong>)
    else if (m[3]) out.push(<em key={k++}>{m[3].slice(1, -1)}</em>)
    last = m.index + m[0].length
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

export function CodeBlock({ code, lang, modifies }: { code: string; lang: string; modifies: boolean }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    try { await navigator.clipboard.writeText(code); setCopied(true); setTimeout(() => setCopied(false), 1500) } catch { /* clipboard blocked */ }
  }
  return (
    <div className={`rounded-lg border overflow-hidden ${modifies ? 'border-critical/50' : 'border-border'}`}>
      <div className={`flex items-center justify-between gap-2 px-3 py-1.5 border-b text-xs ${modifies ? 'bg-critical-bg border-critical/40' : 'bg-surface-2 border-border'}`}>
        <div className="flex items-center gap-2">
          {modifies
            ? <Badge tone="critical"><ShieldAlert size={11} /> Modifies environment</Badge>
            : <Badge tone="ok"><Eye size={11} /> Read only</Badge>}
          {lang ? <span className="font-mono text-faint">{lang}</span> : null}
        </div>
        <button onClick={copy} className="inline-flex items-center gap-1 text-muted hover:text-fg transition-colors">
          {copied ? <Check size={13} /> : <Copy size={13} />}{copied ? 'Copied' : 'Copy'}
        </button>
      </div>
      <pre className="overflow-x-auto bg-bg px-3.5 py-3 text-[12.5px] leading-relaxed font-mono text-fg"><code>{code}</code></pre>
      {modifies ? <div className="px-3 py-1.5 text-[11px] text-critical bg-critical-bg/60 border-t border-critical/30">Review before running. VCF Doctor never executes scripts.</div> : null}
    </div>
  )
}

export function Markdown({ text }: { text: string }) {
  const blocks = useMemo(() => parseMarkdown(text), [text])
  return (
    <div className="prose-md">
      {blocks.map((b, i) => {
        switch (b.kind) {
          case 'heading': {
            const Tag = (`h${Math.min(3, Math.max(1, b.level))}`) as 'h1' | 'h2' | 'h3'
            return <Tag key={i}>{renderInline(b.text)}</Tag>
          }
          case 'para': return <p key={i}>{renderInline(b.text)}</p>
          case 'ul': return <ul key={i}>{b.items.map((it, j) => <li key={j}>{renderInline(it)}</li>)}</ul>
          case 'ol': return <ol key={i}>{b.items.map((it, j) => <li key={j}>{renderInline(it)}</li>)}</ol>
          case 'code': return <CodeBlock key={i} code={b.code} lang={b.lang} modifies={b.modifies} />
        }
      })}
    </div>
  )
}
