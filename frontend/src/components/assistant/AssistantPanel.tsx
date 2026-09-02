import { useEffect, useRef, useState } from 'react'
import { AlertCircle, Bot, Send, Sparkles, Square, User } from 'lucide-react'
import type { AssistantEvidenceCount, AssistantRequest, AssistantTask, Change, Event, Finding, Resource, ScriptFormat } from '@/types'
import { getAssistantStatus, streamAssistant } from '@/api'
import { useAsync } from '@/hooks/useAsync'
import { Button, Select, Skeleton } from '@/components/ui'
import { Markdown } from './Markdown'
import { SeverityBadge } from '@/components/domain'
import { cn } from '@/lib/cn'
import type { AssistantSeed } from '@/state/AssistantState'

interface Message {
  id: number
  role: 'user' | 'assistant'
  text: string
  task?: AssistantTask
  evidence?: AssistantEvidenceCount
  stopReason?: string
  error?: string
  streaming?: boolean
}

const STARTERS: Array<{ label: string; task: AssistantTask; question: string }> = [
  { label: 'Explain this finding', task: 'explain', question: 'Explain this finding and why it matters.' },
  { label: 'What changed around this failure?', task: 'investigate', question: 'What changed around this failure and which change is the most likely cause?' },
  { label: 'How should I investigate this?', task: 'investigate', question: 'Give me an ordered investigation plan.' },
  { label: 'Generate a PowerCLI investigation script', task: 'generate-script', question: 'Generate a PowerCLI investigation script.' },
  { label: 'Generate REST API commands', task: 'generate-script', question: 'Generate REST API commands to investigate.' },
]

const TASK_LABEL: Record<AssistantTask, string> = { explain: 'Explain', investigate: 'Investigate', 'generate-script': 'Generate script', ask: 'Ask' }

export function AssistantPanel({ seed, seedKey, context, compact }: {
  seed: AssistantSeed | null
  seedKey: number
  context: { findings: Finding[]; changes: Change[]; resources: Resource[]; events: Event[] }
  compact?: boolean
}) {
  const status = useAsync(() => getAssistantStatus(), [seedKey])
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [task, setTask] = useState<AssistantTask>('ask')
  const [format, setFormat] = useState<ScriptFormat>('powercli')
  const [busy, setBusy] = useState(false)
  const [ctx, setCtx] = useState(context)
  const abortRef = useRef<AbortController | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const idRef = useRef(1)
  const appliedSeed = useRef(-1)

  // Fall back to page context if no seed context is set.
  useEffect(() => { if (!seed) setCtx(context) }, [context, seed])

  useEffect(() => {
    if (!seed || appliedSeed.current === seedKey) return
    appliedSeed.current = seedKey
    const next = { findings: seed.findings ?? context.findings, changes: seed.changes ?? context.changes, resources: seed.resources ?? context.resources, events: seed.events ?? context.events }
    setCtx(next)
    setTask(seed.task)
    if (seed.scriptFormat) setFormat(seed.scriptFormat)
    if (seed.question) setInput(seed.question)
    if (seed.autoSend && seed.question) void send(seed.task, seed.question, next, seed.scriptFormat ?? format)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seed, seedKey])

  useEffect(() => { scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' }) }, [messages])

  // Closing the drawer unmounts the panel; abort the stream so the backend stops generating.
  useEffect(() => () => abortRef.current?.abort(), [])

  const available = status.data?.available ?? false

  async function send(t: AssistantTask, question: string, c = ctx, f = format) {
    if (!question.trim() || busy) return
    const userMsg: Message = { id: idRef.current++, role: 'user', text: question, task: t }
    const asst: Message = { id: idRef.current++, role: 'assistant', text: '', task: t, streaming: true }
    setMessages(m => [...m, userMsg, asst])
    setInput('')
    setBusy(true)
    const req: AssistantRequest = {
      task: t,
      ...(t === 'generate-script' ? { script_format: f } : {}),
      context: { question, findings: c.findings, changes: c.changes, resources: c.resources, events: c.events, allowed_actions: ['read'] },
    }
    const ac = new AbortController()
    abortRef.current = ac
    const update = (fn: (m: Message) => Message) => setMessages(ms => ms.map(m => (m.id === asst.id ? fn(m) : m)))
    try {
      await streamAssistant(req, ev => {
        if (ev.type === 'delta') update(m => ({ ...m, text: m.text + ev.text }))
        else if (ev.type === 'done') update(m => ({ ...m, evidence: ev.evidence, stopReason: ev.stop_reason, streaming: false }))
        else update(m => ({ ...m, error: ev.message, streaming: false }))
      }, ac.signal)
    } catch (e) {
      if (!(e instanceof DOMException && e.name === 'AbortError')) update(m => ({ ...m, error: e instanceof Error ? e.message : String(e), streaming: false }))
    } finally {
      update(m => ({ ...m, streaming: false }))
      setBusy(false)
      abortRef.current = null
    }
  }

  const stop = () => abortRef.current?.abort()
  const hasFinding = ctx.findings.length > 0
  const total = ctx.findings.length + ctx.changes.length + ctx.resources.length + ctx.events.length

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* Context strip */}
      <div className="px-5 py-3 border-b border-border bg-surface-2/60 shrink-0">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div className="flex items-center gap-2 text-[13px]">
            <Sparkles size={14} className="text-accent" />
            <span className="text-muted">Using:</span>
            <span className="font-medium tnum">{ctx.findings.length} {ctx.findings.length === 1 ? 'finding' : 'findings'}</span>
            <span className="text-faint">,</span>
            <span className="font-medium tnum">{ctx.changes.length} {ctx.changes.length === 1 ? 'change' : 'changes'}</span>
            <span className="text-faint">,</span>
            <span className="font-medium tnum">{ctx.resources.length} {ctx.resources.length === 1 ? 'resource' : 'resources'}</span>
            <span className="text-faint">,</span>
            <span className="font-medium tnum">{ctx.events.length} {ctx.events.length === 1 ? 'event' : 'events'}</span>
          </div>
          {status.data ? <span className="text-xs text-faint font-mono">{status.data.provider} / {status.data.model}</span> : <Skeleton className="h-3 w-24" />}
        </div>
        {hasFinding ? (
          <div className="mt-2 flex items-center gap-2 text-[13px] min-w-0">
            <SeverityBadge severity={ctx.findings[0].severity} />
            <span className="truncate font-medium">{ctx.findings[0].title}</span>
          </div>
        ) : total === 0 ? <p className="mt-1.5 text-xs text-faint">No evidence loaded. Open a finding on the Health page for a grounded answer.</p> : null}
      </div>

      {/* Unavailable banner */}
      {status.data && !available ? (
        <div className="mx-5 mt-4 rounded-lg border border-warning/40 bg-warning-bg px-4 py-3 text-sm flex gap-2.5 shrink-0">
          <AlertCircle size={16} className="text-warning shrink-0 mt-0.5" />
          <div>
            <p className="font-medium text-fg">Assistant unavailable</p>
            <p className="text-muted mt-0.5">{status.data.reason ?? 'The assistant provider is not configured.'} Health and Changes keep working without it; configure a provider in Settings.</p>
          </div>
        </div>
      ) : status.error ? (
        <div className="mx-5 mt-4 rounded-lg border border-critical/40 bg-critical-bg px-4 py-3 text-sm flex gap-2.5 shrink-0">
          <AlertCircle size={16} className="text-critical shrink-0 mt-0.5" />
          <div><p className="font-medium">Could not reach the assistant</p><p className="text-muted mt-0.5 font-mono text-xs">{status.error.message}</p></div>
        </div>
      ) : null}

      {/* Transcript */}
      <div ref={scrollRef} className="flex-1 min-h-0 overflow-y-auto px-5 py-5 space-y-5">
        {messages.length === 0 ? (
          <div className={cn('anim-fade-up', compact ? '' : 'max-w-2xl')}>
            <div className="h-10 w-10 rounded-lg bg-surface-2 border border-border flex items-center justify-center text-accent mb-4"><Bot size={20} /></div>
            <h3 className="text-base font-semibold tracking-tight">Evidence-grounded assistant</h3>
            <p className="text-sm text-muted mt-1 leading-relaxed">Answers come only from the findings, changes, resources and vCenter events shown above. Scripts are produced for your review and are never executed.</p>
            <div className="mt-5 grid gap-2">
              {STARTERS.map(s => (
                <button key={s.label} disabled={!available || busy}
                  onClick={() => { setTask(s.task); void send(s.task, s.question) }}
                  className="text-left rounded-lg border border-border bg-surface px-4 py-2.5 text-sm hover:border-border-strong hover:bg-surface-2 transition-colors disabled:opacity-50 flex items-center justify-between gap-3">
                  <span>{s.label}</span>
                  <span className="text-[11px] uppercase tracking-wider text-faint font-semibold">{TASK_LABEL[s.task]}</span>
                </button>
              ))}
            </div>
          </div>
        ) : messages.map(m => <MessageView key={m.id} m={m} />)}
      </div>

      {/* Composer */}
      <div className="border-t border-border px-5 py-4 shrink-0 bg-surface">
        <div className="flex items-center gap-2 mb-2.5 flex-wrap">
          <Select value={task} onChange={e => setTask(e.target.value as AssistantTask)} className="h-8 text-[13px]" disabled={busy}>
            <option value="ask">Ask</option>
            <option value="explain">Explain</option>
            <option value="investigate">Investigate</option>
            <option value="generate-script">Generate script</option>
          </Select>
          {task === 'generate-script' ? (
            <Select value={format} onChange={e => setFormat(e.target.value as ScriptFormat)} className="h-8 text-[13px]" disabled={busy}>
              <option value="powercli">PowerCLI</option>
              <option value="python">Python</option>
              <option value="shell">Shell</option>
              <option value="rest">REST</option>
            </Select>
          ) : null}
        </div>
        <form className="flex items-end gap-2" onSubmit={e => { e.preventDefault(); void send(task, input) }}>
          <textarea
            value={input} onChange={e => setInput(e.target.value)} rows={2} disabled={!available || busy}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void send(task, input) } }}
            placeholder={available ? 'Ask about the evidence in view. Enter to send, Shift+Enter for a new line.' : 'Assistant unavailable'}
            className="flex-1 resize-none rounded-md border border-border bg-bg px-3 py-2 text-sm placeholder:text-faint focus:outline-none focus:border-accent focus:ring-2 focus:ring-accent/25 disabled:opacity-50"
          />
          {busy
            ? <Button type="button" variant="secondary" onClick={stop} aria-label="Stop"><Square size={14} /> Stop</Button>
            : <Button type="submit" variant="primary" disabled={!available || !input.trim()} aria-label="Send"><Send size={14} /> Send</Button>}
        </form>
      </div>
    </div>
  )
}

function MessageView({ m }: { m: Message }) {
  if (m.role === 'user') {
    return (
      <div className="flex gap-3 anim-fade-up">
        <div className="h-7 w-7 rounded-md bg-surface-3 flex items-center justify-center text-muted shrink-0"><User size={14} /></div>
        <div className="min-w-0">
          <div className="text-[11px] uppercase tracking-wider text-faint font-semibold mb-1">{m.task ? TASK_LABEL[m.task] : 'You'}</div>
          <p className="text-sm">{m.text}</p>
        </div>
      </div>
    )
  }
  const refused = m.stopReason === 'refusal'
  return (
    <div className="flex gap-3 anim-fade-up">
      <div className="h-7 w-7 rounded-md bg-info-bg flex items-center justify-center text-accent shrink-0"><Bot size={14} /></div>
      <div className="min-w-0 flex-1">
        <div className="text-[11px] uppercase tracking-wider text-faint font-semibold mb-1.5">Assistant</div>
        {m.text ? <Markdown text={m.text} /> : m.streaming ? <div className="flex items-center gap-1.5 h-5"><span className="h-1.5 w-1.5 rounded-full bg-muted anim-pulse" /><span className="h-1.5 w-1.5 rounded-full bg-muted anim-pulse [animation-delay:200ms]" /><span className="h-1.5 w-1.5 rounded-full bg-muted anim-pulse [animation-delay:400ms]" /></div> : null}
        {refused ? <p className="mt-2 text-sm text-warning">The model declined to answer this request. Try rephrasing or narrowing the evidence.</p> : null}
        {m.error ? <p className="mt-2 text-sm text-critical flex items-center gap-1.5"><AlertCircle size={14} />{m.error}</p> : null}
        {m.evidence && !m.streaming ? (
          <p className="mt-3 text-xs text-faint">Using: {m.evidence.findings} {m.evidence.findings === 1 ? 'finding' : 'findings'}, {m.evidence.changes} {m.evidence.changes === 1 ? 'change' : 'changes'}, {m.evidence.resources} {m.evidence.resources === 1 ? 'resource' : 'resources'}, {m.evidence.events} {m.evidence.events === 1 ? 'event' : 'events'}</p>
        ) : null}
      </div>
    </div>
  )
}
