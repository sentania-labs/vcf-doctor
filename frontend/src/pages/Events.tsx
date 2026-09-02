import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ScrollText, Search } from 'lucide-react'
import type { Event, EventCategory } from '@/types'
import { getEvents } from '@/api'
import { useAsync } from '@/hooks/useAsync'
import { useAppState } from '@/state/AppState'
import { Badge, Button, Card, EmptyState, ErrorState, Input, PageHeader, Segmented, Skeleton } from '@/components/ui'
import { EventCategoryDot, ResourceIcon, eventCategoryLabel } from '@/components/domain'
import { RANGE_PRESETS, formatDateTime, formatTime, groupByDay, relativeTime, sinceFor, type RangePreset } from '@/lib/format'
import { cn } from '@/lib/cn'

type CategoryFilter = 'all' | EventCategory
const PAGE = 100
const MAX_LIMIT = 5000 // backend ceiling for ?limit=

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value)
  useEffect(() => { const id = setTimeout(() => setV(value), ms); return () => clearTimeout(id) }, [value, ms])
  return v
}

export default function EventsPage() {
  const { connectionId, connections, setSelectedId, refreshKey } = useAppState()
  const nav = useNavigate()
  const [range, setRange] = useState<RangePreset>('24h')
  const [category, setCategory] = useState<CategoryFilter>('all')
  const [q, setQ] = useState('')
  const query = useDebounced(q.trim(), 300)
  const [limit, setLimit] = useState(PAGE)
  const since = useMemo(() => sinceFor(range), [range, refreshKey, connectionId]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setLimit(PAGE) }, [connectionId, range, category, query])
  const events = useAsync(
    () => getEvents({ connectionId, since, category: category === 'all' ? null : category, q: query || null, limit }),
    [connectionId, since, category, query, limit, refreshKey], !!connectionId)

  if (!connectionId) {
    return (
      <div className="anim-fade-up">
        <PageHeader title="Events" subtitle="What vCenter recorded: events and tasks, per connection" />
        <Card>
          <EmptyState icon={<ScrollText size={20} />} title="Pick a connection"
            body="Events are collected per vCenter. Choose one to see what it recorded."
            action={connections.length > 0 ? (
              <div className="flex flex-wrap gap-2 justify-center">
                {connections.map(c => <Button key={c.id} onClick={() => setSelectedId(c.id)}>{c.name}</Button>)}
              </div>
            ) : <Button variant="primary" onClick={() => nav('/connections')}>Add vCenter</Button>} />
        </Card>
      </div>
    )
  }

  const rows = events.data ?? []
  const groups = groupByDay(rows, e => e.time)
  const counts: Record<EventCategory, number> = { user: 0, warning: 0, error: 0, info: 0 }
  for (const e of rows) counts[e.category] = (counts[e.category] ?? 0) + 1
  const rangeLabel = RANGE_PRESETS.find(p => p.value === range)?.label ?? range
  const filtered = category !== 'all' || !!query

  return (
    <div className="anim-fade-up">
      <PageHeader title="Events" subtitle="What vCenter recorded: events and tasks, newest first"
        actions={<Segmented value={range} onChange={setRange} options={RANGE_PRESETS.map(p => ({ value: p.value, label: `Last ${p.label}` }))} />} />

      <div className="flex flex-wrap items-center gap-3 mb-5">
        <Segmented value={category} onChange={setCategory} options={[
          { value: 'all', label: 'All' },
          { value: 'user', label: <><EventCategoryDot category="user" className="h-1.5 w-1.5" />User actions</> },
          { value: 'warning', label: <><EventCategoryDot category="warning" className="h-1.5 w-1.5" />Warnings</> },
          { value: 'error', label: <><EventCategoryDot category="error" className="h-1.5 w-1.5" />Errors</> },
          { value: 'info', label: <><EventCategoryDot category="info" className="h-1.5 w-1.5" />Info</> },
        ]} />
        <div className="relative"><Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-faint" /><Input value={q} onChange={e => setQ(e.target.value)} placeholder="Search message, type, user or resource" className="pl-8 w-80" aria-label="Search events" /></div>
        {events.data ? (
          <div className="ml-auto flex flex-wrap items-baseline gap-x-4 gap-y-1 text-sm">
            <span className="text-xl font-semibold tracking-tight tnum">{rows.length}{rows.length >= limit ? '+' : ''} <span className="text-sm text-muted font-medium">{rows.length === 1 ? 'event' : 'events'}</span></span>
            {category === 'all' ? (['error', 'warning', 'user', 'info'] as EventCategory[]).filter(c => counts[c] > 0).map(c => (
              <span key={c} className="inline-flex items-center gap-1.5 text-muted"><EventCategoryDot category={c} /><span className="font-semibold text-fg tnum">{counts[c]}</span> {eventCategoryLabel[c].toLowerCase()}</span>
            )) : null}
          </div>
        ) : null}
      </div>

      {events.error ? <Card><ErrorState title="Events unavailable" error={events.error} onRetry={events.reload} /></Card>
        : events.loading && !events.data ? <div className="space-y-2">{[0, 1, 2, 3, 4, 5].map(i => <Skeleton key={i} className="h-12" />)}</div>
        : rows.length === 0 ? (
          <Card><EmptyState icon={<ScrollText size={20} />} title={filtered ? `No matching events in the last ${rangeLabel}` : `No events in the last ${rangeLabel}`}
            body={filtered ? 'Try another category, clear the search, or widen the time range.' : 'The collector fetches vCenter events and tasks on every scan. Nothing was recorded in this window yet.'}
            action={<div className="flex gap-2">{filtered ? <Button onClick={() => { setCategory('all'); setQ('') }}>Clear filters</Button> : null}{range !== '7d' ? <Button onClick={() => setRange('7d')}>Last 7 days</Button> : null}</div>} /></Card>
        ) : (
          <div className="space-y-7">
            {groups.map(g => (
              <section key={g.key}>
                <h2 className="flex items-baseline gap-2 text-xs font-semibold uppercase tracking-wider text-faint mb-2.5">{g.label} <span className="tnum font-medium normal-case tracking-normal">{g.items.length}</span></h2>
                <Card className="divide-y divide-border overflow-hidden">
                  {g.items.map(e => <EventRow key={e.id} e={e} onResource={id => nav(`/inventory?resource=${encodeURIComponent(id)}`)} />)}
                </Card>
              </section>
            ))}
            {rows.length >= limit && limit < MAX_LIMIT ? (
              <div className="flex justify-center"><Button onClick={() => setLimit(l => Math.min(l + PAGE, MAX_LIMIT))} loading={events.loading}>Load more</Button></div>
            ) : null}
          </div>
        )}
    </div>
  )
}

function EventRow({ e, onResource }: { e: Event; onResource: (id: string) => void }) {
  return (
    <div className="px-4 py-2.5 grid grid-cols-[64px_10px_minmax(0,1fr)_minmax(0,170px)_minmax(0,200px)] items-start gap-3">
      <span className="text-[13px] text-muted tnum pt-0.5 whitespace-nowrap" title={formatDateTime(e.time)}>{relativeTime(e.time)}</span>
      <EventCategoryDot category={e.category} className="mt-2" />
      <div className="min-w-0">
        <p className={cn('text-sm leading-snug', e.category === 'error' ? 'text-fg font-medium' : 'text-fg')}>{e.message}</p>
        <p className="mt-0.5 text-xs text-faint flex items-center gap-2 min-w-0">
          <span className="font-mono truncate" title={e.type}>{e.type}</span>
          {e.source === 'task' ? <Badge tone="neutral" className="normal-case tracking-normal">task</Badge> : null}
          <span className="tnum">{formatTime(e.time)}</span>
        </p>
      </div>
      <span className="text-[13px] text-muted truncate pt-0.5" title={e.user ?? undefined}>{e.user ?? <span className="text-faint">system</span>}</span>
      <span className="min-w-0 pt-0.5">
        {e.resource_id ? (
          <button onClick={() => onResource(e.resource_id as string)} className="inline-flex items-center gap-1.5 text-[13px] text-accent hover:underline max-w-full" title="Open in Inventory">
            <ResourceIcon type={e.resource_type ?? ''} size={13} className="shrink-0" /><span className="truncate">{e.resource_name ?? e.resource_id}</span>
          </button>
        ) : e.resource_name ? <span className="text-[13px] text-muted truncate inline-block max-w-full">{e.resource_name}</span> : null}
      </span>
    </div>
  )
}
