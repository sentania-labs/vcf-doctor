import { useEffect, useState } from 'react'
import { Bot, ChevronDown, RefreshCw } from 'lucide-react'
import { ALL, useAppState } from '@/state/AppState'
import { useAssistantDrawer } from '@/state/AssistantState'
import { Button } from '@/components/ui'
import { relativeTime } from '@/lib/format'
import { cn } from '@/lib/cn'

export function TopBar() {
  const { connections, selectedId, setSelectedId, selected, lastScan, scanning, scanNow, scanError, scans, backend } = useAppState()
  const { openDrawer } = useAssistantDrawer()
  const [, tick] = useState(0)
  useEffect(() => { const id = setInterval(() => tick(t => t + 1), 5000); return () => clearInterval(id) }, [])

  // Connection status from the latest scan per connection.
  const latestByConn = new Map<string, typeof scans[number]>()
  for (const s of [...scans].sort((a, b) => a.started.localeCompare(b.started))) latestByConn.set(s.connection_id, s)
  const relevant = selectedId === ALL ? [...latestByConn.values()] : [latestByConn.get(selectedId)].filter(Boolean) as typeof scans
  let dot: 'ok' | 'error' | 'running' | 'none' = 'none'
  let statusLabel = 'Not scanned yet'
  if (backend === 'down') { dot = 'error'; statusLabel = 'Backend down' }
  else if (relevant.some(s => s.status === 'running')) { dot = 'running'; statusLabel = 'Scanning' }
  else if (relevant.length && relevant.every(s => s.status === 'ok')) { dot = 'ok'; statusLabel = 'Connected' }
  else if (relevant.some(s => s.status === 'error')) { dot = 'error'; statusLabel = selectedId === ALL ? 'A connection is failing' : 'Last scan failed' }
  const dotCls = { ok: 'bg-ok', error: 'bg-critical', running: 'bg-warning anim-pulse', none: 'bg-faint' }[dot]

  return (
    <header className="h-16 shrink-0 border-b border-border bg-surface/80 backdrop-blur px-6 flex items-center gap-5">
      <div className="relative">
        <select value={selectedId} onChange={e => setSelectedId(e.target.value)}
          className="appearance-none h-9 pl-3 pr-9 rounded-md border border-border bg-bg text-sm font-medium hover:border-border-strong focus:outline-none focus:border-accent transition-colors min-w-[220px]">
          <option value={ALL}>All connections{connections.length ? ` (${connections.length})` : ''}</option>
          {connections.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        <ChevronDown size={14} className="absolute right-3 top-1/2 -translate-y-1/2 text-muted pointer-events-none" />
      </div>
      {selected ? <span className="text-xs text-faint font-mono hidden lg:inline">{selected.host}</span> : null}

      <div className="ml-auto flex items-center gap-5 text-sm">
        <span className="text-muted hidden md:inline">Last scan: <span className="text-fg tnum">{relativeTime(lastScan)}</span></span>
        <span className="inline-flex items-center gap-2 text-muted">
          <span className={cn('h-2 w-2 rounded-full', dotCls)} />
          <span className="text-fg">{statusLabel}</span>
        </span>
        {scanError ? <span className="text-xs text-critical max-w-[240px] truncate" title={scanError}>{scanError}</span> : null}
        <Button variant="ghost" size="md" onClick={() => openDrawer()} title="Open assistant"><Bot size={16} /> Assistant</Button>
        <Button variant="primary" onClick={() => void scanNow()} loading={scanning} disabled={backend === 'down' || connections.length === 0}>
          {!scanning ? <RefreshCw size={15} /> : null}{scanning ? 'Scanning' : 'Scan Now'}
        </Button>
      </div>
    </header>
  )
}
