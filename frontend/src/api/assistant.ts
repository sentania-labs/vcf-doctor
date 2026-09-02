import type { AssistantRequest, AssistantStatus, AssistantStreamEvent } from '@/types'
import { apiGet, apiUrl, ApiError } from './client'
import { USE_MOCKS, delay, mockState, mockAssistantText } from './mocks'

export function getAssistantStatus(): Promise<AssistantStatus> {
  if (USE_MOCKS) return delay(mockState.assistantStatus, 100)
  return apiGet<AssistantStatus>('/assistant/status')
}

export type AssistantEventHandler = (ev: AssistantStreamEvent) => void

// Streams POST /assistant as server-sent events. EventSource cannot POST, so this parses the body by hand.
export async function streamAssistant(req: AssistantRequest, onEvent: AssistantEventHandler, signal?: AbortSignal): Promise<void> {
  if (USE_MOCKS) return mockStream(req, onEvent, signal)

  const r = await fetch(apiUrl('/assistant'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(req),
    signal,
  })
  if (!r.ok) {
    let detail = `${r.status} ${r.statusText}`
    try { const b = await r.json(); if (typeof b?.detail === 'string') detail = b.detail } catch { /* ignore */ }
    throw new ApiError(r.status, detail)
  }
  if (!r.body) throw new Error('Empty response body')
  if (!(r.headers.get('content-type') ?? '').toLowerCase().startsWith('text/event-stream')) {
    onEvent({ type: 'error', message: 'Assistant returned an unexpected response' })
    return
  }

  const reader = r.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let sawDone = false
  let sawError = false

  const dispatch = (block: string) => {
    let event = 'message'
    const dataLines: string[] = []
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) event = line.slice(6).trim()
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).replace(/^ /, ''))
    }
    if (dataLines.length === 0) return
    const raw = dataLines.join('\n')
    let data: Record<string, unknown> = {}
    try { data = JSON.parse(raw) } catch { data = { text: raw } }
    if (event === 'delta') onEvent({ type: 'delta', text: String(data.text ?? '') })
    else if (event === 'done') {
      sawDone = true
      const ev = (data.evidence ?? {}) as Record<string, number>
      onEvent({ type: 'done', stop_reason: String(data.stop_reason ?? 'end_turn'), evidence: { findings: ev.findings ?? 0, changes: ev.changes ?? 0, resources: ev.resources ?? 0, events: ev.events ?? 0 } })
    } else if (event === 'error') {
      sawError = true
      onEvent({ type: 'error', message: String(data.message ?? 'Assistant error') })
    }
  }

  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    buffer = buffer.replace(/\r\n/g, '\n')
    let idx: number
    while ((idx = buffer.indexOf('\n\n')) >= 0) {
      const block = buffer.slice(0, idx)
      buffer = buffer.slice(idx + 2)
      if (block.trim()) dispatch(block)
    }
  }
  // An unterminated trailing block means the connection was cut mid-event; do not show it as an answer.
  // A stream that ends with neither done nor error (proxy timeout, backend crash) is an error, not a clean finish.
  if (!sawDone && !sawError) onEvent({ type: 'error', message: 'Stream ended before completion' })
}

async function mockStream(req: AssistantRequest, onEvent: AssistantEventHandler, signal?: AbortSignal): Promise<void> {
  const status = mockState.assistantStatus
  if (!status.available) {
    onEvent({ type: 'error', message: status.reason ?? 'Assistant unavailable' })
    return
  }
  const text = mockAssistantText(req.task, req.script_format, req.context.findings[0]?.title)
  const chunks = text.match(/[\s\S]{1,14}/g) ?? []
  for (const c of chunks) {
    if (signal?.aborted) return
    await new Promise(r => setTimeout(r, 12))
    onEvent({ type: 'delta', text: c })
  }
  onEvent({ type: 'done', stop_reason: 'end_turn', evidence: { findings: req.context.findings.length, changes: req.context.changes.length, resources: req.context.resources.length, events: req.context.events?.length ?? 0 } })
}

export interface AssistantModel { id: string; display_name: string; recommended: boolean }
export async function getAssistantModels(): Promise<{ models: AssistantModel[]; source: 'live' | 'curated' }> {
  if (USE_MOCKS) return delay({ models: [
    { id: 'claude-opus-5', display_name: 'Claude Opus 5', recommended: true },
    { id: 'claude-sonnet-5', display_name: 'Claude Sonnet 5', recommended: false },
    { id: 'claude-haiku-4-5', display_name: 'Claude Haiku 4.5', recommended: false },
  ], source: 'curated' as const }, 80)
  return apiGet('/assistant/models')
}
