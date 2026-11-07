import { useEffect, useState } from 'react'
import { Bot, ChevronDown, RefreshCw } from 'lucide-react'
import { ALL, useAppState } from '@/state/AppState'
import { useAssistantDrawer } from '@/state/AssistantState'
import { useAuth } from '@/state/AuthState'
import { databaseOutage } from '@/state/databaseOutage'
import { Button } from '@/components/ui'
import { relativeTime } from '@/lib/format'
import { cn } from '@/lib/cn'
import { canStartScan, topBarStatus } from './topBarState'

export function TopBar() {
  const { connections, selectedId, setSelectedId, selected, lastScan, scanning, scanNow, scanError, scans, backend, databaseHealthy } = useAppState()
  const { status: authStatus } = useAuth()
  const databaseDown = databaseOutage(authStatus, databaseHealthy, backend)
  const { openDrawer } = useAssistantDrawer()
  const [, tick] = useState(0)
  useEffect(() => { const id = setInterval(() => tick(t => t + 1), 5000); return () => clearInterval(id) }, [])

  // Connection status from the latest scan per connection.
  const latestByConn = new Map<string, typeof scans[number]>()
  for (const s of [...scans].sort((a, b) => a.started.localeCompare(b.started))) latestByConn.set(s.connection_id, s)
  const relevant = selectedId === ALL ? [...latestByConn.values()] : [latestByConn.get(selectedId)].filter(Boolean) as typeof scans
  const { dot, label: statusLabel } = topBarStatus(
    backend,
    relevant.map(scan => scan.status),
    selectedId === ALL,
    databaseDown,
  )
  const dotCls = { ok: 'bg-ok', error: 'bg-critical', running: 'bg-warning anim-pulse', none: 'bg-faint' }[dot]

  return (
    <header className="min-h-16 py-2 shrink-0 border-b border-border bg-surface/80 backdrop-blur px-6 flex flex-wrap items-center gap-5">
      <div className="relative">
        <select value={selectedId} disabled={databaseDown} onChange={e => setSelectedId(e.target.value)}
          className="appearance-none h-9 pl-3 pr-9 rounded-md border border-border bg-bg text-sm font-medium hover:border-border-strong focus:outline-none focus:border-accent transition-colors min-w-[220px]">
          <option value={ALL}>All connections{connections.length ? ` (${connections.length})` : ''}</option>
          {connections.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        <ChevronDown size={14} className="absolute right-3 top-1/2 -translate-y-1/2 text-muted pointer-events-none" />
      </div>
      {selected ? <span className="text-xs text-faint font-mono whitespace-nowrap hidden lg:inline">{selected.host}</span> : null}

      <div className="ml-auto flex flex-wrap items-center gap-5 text-sm">
        <span className="text-muted whitespace-nowrap hidden min-[960px]:inline">Last scan: <span className="text-fg tnum">{relativeTime(lastScan)}</span></span>
        <span className="inline-flex items-center gap-2 text-muted whitespace-nowrap">
          <span className={cn('h-2 w-2 rounded-full', dotCls)} />
          <span className="text-fg">{statusLabel}</span>
        </span>
        {scanError ? <span className="text-xs text-critical max-w-[240px] truncate" title={scanError}>{scanError}</span> : null}
        <div className="flex items-center gap-3 shrink-0">
          <Button variant="ghost" size="md" className="whitespace-nowrap" onClick={() => openDrawer()} disabled={databaseDown} title={databaseDown ? 'Unavailable while the database is unreachable' : 'Open assistant'}><Bot size={16} /> Assistant</Button>
          <Button variant="primary" className="whitespace-nowrap" onClick={() => void scanNow()} loading={scanning} disabled={!canStartScan(backend, connections.length, databaseDown)}>
            {!scanning ? <RefreshCw size={15} /> : null}{scanning ? 'Scanning' : 'Scan Now'}
          </Button>
        </div>
      </div>
    </header>
  )
}
