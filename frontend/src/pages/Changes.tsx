import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { AlertTriangle, ArrowDown, Camera, ChevronDown, ChevronRight, GitCompareArrows, History } from 'lucide-react'
import type { Change, ChangeLogEntry, Significance, SnapshotSummary } from '@/types'
import { getChangeLog, getChanges, getChangesMinSignificance, getSnapshots } from '@/api'
import { useAsync } from '@/hooks/useAsync'
import { useAppState } from '@/state/AppState'
import { Badge, Button, Card, EmptyState, ErrorState, Input, PageHeader, Segmented, Select, Skeleton } from '@/components/ui'
import { PropertyChanges, ChangeRow, ResourceIcon, ResourceTypeLabel, significanceLabel, significanceTone, snapshotTier, tierLabel } from '@/components/domain'
import { RANGE_PRESETS, formatDateTime, formatTime, groupByDay, relativeTime, sinceFor, type RangePreset } from '@/lib/format'
import { cn } from '@/lib/cn'

type View = 'timeline' | 'compare'
const PAGE = 200
const MAX_LIMIT = 5000 // backend ceiling for ?limit=

export default function ChangesPage() {
  const { connectionId, connections, setSelectedId, refreshKey } = useAppState()
  const nav = useNavigate()
  const [params, setParams] = useSearchParams()
  const view: View = params.get('view') === 'compare' ? 'compare' : 'timeline'
  const setView = (v: View) => setParams(p => { if (v === 'timeline') p.delete('view'); else p.set('view', v); return p }, { replace: true })

  // Significance floor: starts from the Settings default, then follows the control on this page (both tabs).
  const [minSig, setMinSig] = useState<Significance | null>(null)
  useEffect(() => {
    let cancelled = false
    getChangesMinSignificance().then(v => { if (!cancelled) setMinSig(v) })
    return () => { cancelled = true }
  }, [])

  if (!connectionId) {
    return (
      <div className="anim-fade-up">
        <PageHeader title="What Changed?" subtitle="The change log and snapshot comparisons for one vCenter" />
        <Card>
          <EmptyState icon={<GitCompareArrows size={20} />} title="Pick a connection"
            body="Changes are recorded per vCenter, so the time machine works on one connection at a time. Choose one to continue."
            action={connections.length > 0 ? (
              <div className="flex flex-wrap gap-2 justify-center">
                {connections.map(c => <Button key={c.id} onClick={() => setSelectedId(c.id)}>{c.name}</Button>)}
              </div>
            ) : <Button variant="primary" onClick={() => nav('/connections')}>Add vCenter</Button>} />
        </Card>
      </div>
    )
  }

  return (
    <div className="anim-fade-up">
      <PageHeader title="What Changed?" subtitle={view === 'timeline' ? 'Every difference each scan recorded, newest first' : 'Semantic differences between two snapshots, most important first'}
        actions={minSig ? (
          <Segmented value={minSig} onChange={setMinSig} options={[
            { value: 'low', label: 'All' },
            { value: 'medium', label: <><span className="h-1.5 w-1.5 rounded-full bg-warning" />Medium and above</> },
            { value: 'high', label: <><span className="h-1.5 w-1.5 rounded-full bg-critical" />High only</> },
          ]} />
        ) : null} />

      <div role="tablist" className="flex items-center gap-1 border-b border-border mb-6">
        <Tab active={view === 'timeline'} onClick={() => setView('timeline')} icon={<History size={15} />}>Timeline</Tab>
        <Tab active={view === 'compare'} onClick={() => setView('compare')} icon={<GitCompareArrows size={15} />}>Compare snapshots</Tab>
      </div>

      {view === 'timeline'
        ? <Timeline connectionId={connectionId} minSig={minSig} setMinSig={setMinSig} refreshKey={refreshKey} onCompare={(from, to) => setParams(p => { p.set('view', 'compare'); p.set('from', from); p.set('to', to); return p }, { replace: true })} />
        : <Compare connectionId={connectionId} minSig={minSig} setMinSig={setMinSig} refreshKey={refreshKey} initialFrom={params.get('from')} initialTo={params.get('to')} />}
    </div>
  )
}

function Tab({ active, onClick, icon, children }: { active: boolean; onClick: () => void; icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <button role="tab" aria-selected={active} onClick={onClick}
      className={cn('inline-flex items-center gap-2 px-3 h-10 text-sm font-medium border-b-2 -mb-px transition-colors',
        active ? 'border-accent text-fg' : 'border-transparent text-muted hover:text-fg')}>
      {icon}{children}
    </button>
  )
}

/* ---------- Timeline: the persisted change log ---------- */

function Timeline({ connectionId, minSig, setMinSig, refreshKey, onCompare }: { connectionId: string; minSig: Significance | null; setMinSig: (s: Significance) => void; refreshKey: number; onCompare: (from: string, to: string) => void }) {
  const [range, setRange] = useState<RangePreset>('24h')
  const [limit, setLimit] = useState(PAGE)
  const since = useMemo(() => sinceFor(range), [range, refreshKey, connectionId]) // eslint-disable-line react-hooks/exhaustive-deps
  const log = useAsync(() => getChangeLog({ connectionId, since, minSignificance: minSig as Significance, limit }), [connectionId, since, minSig, limit, refreshKey], minSig !== null)
  const [openIds, setOpenIds] = useState<Set<string>>(new Set())
  useEffect(() => { setLimit(PAGE); setOpenIds(new Set()) }, [connectionId, range])
  const toggle = (id: string) => setOpenIds(s => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n })

  const rows = useMemo(() => log.data ?? [], [log.data])
  const groups = useMemo(() => groupByDay(rows, r => r.observed_at), [rows])
  const counts: Record<Significance, number> = { high: 0, medium: 0, low: 0 }
  for (const r of rows) counts[r.significance]++
  const rangeLabel = RANGE_PRESETS.find(p => p.value === range)?.label ?? range

  return (
    <div>
      <div className="flex flex-wrap items-center gap-3 mb-5">
        <Segmented value={range} onChange={setRange} options={RANGE_PRESETS.map(p => ({ value: p.value, label: `Last ${p.label}` }))} />
        {log.data ? (
          <div className="flex flex-wrap items-baseline gap-x-5 gap-y-1 ml-auto text-sm">
            <span className="text-xl font-semibold tracking-tight tnum">{rows.length}{rows.length >= limit ? '+' : ''} <span className="text-sm text-muted font-medium">{rows.length === 1 ? 'change' : 'changes'}</span></span>
            <Summary n={counts.high} label="high impact" dot="bg-critical" />
            <Summary n={counts.medium} label="medium" dot="bg-warning" />
            <Summary n={counts.low} label="informational" dot="bg-faint" />
          </div>
        ) : null}
      </div>

      {log.error ? <Card><ErrorState title="Change log unavailable" error={log.error} onRetry={log.reload} /></Card>
        : (log.loading || minSig === null) && !log.data ? <div className="space-y-3">{[0, 1, 2, 3].map(i => <Skeleton key={i} className="h-14" />)}</div>
        : rows.length === 0 ? (
          <Card><EmptyState icon={<History size={20} />} title={minSig !== 'low' ? `No changes at this significance in the last ${rangeLabel}` : `No changes recorded in the last ${rangeLabel}`}
            body={minSig !== 'low' ? 'Lower significance changes may exist. Switch the filter to All, or widen the time range.' : 'Each scan compares against the previous snapshot and records what differs. Nothing differed in this window.'}
            action={<div className="flex gap-2">{minSig !== 'low' ? <Button onClick={() => setMinSig('low')}>Show all</Button> : null}{range !== '7d' ? <Button onClick={() => setRange('7d')}>Last 7 days</Button> : null}</div>} /></Card>
        ) : (
          <div className="space-y-7">
            {groups.map(g => (
              <section key={g.key}>
                <h2 className="flex items-baseline gap-2 text-xs font-semibold uppercase tracking-wider text-faint mb-2.5">{g.label} <span className="tnum font-medium normal-case tracking-normal">{g.items.length}</span></h2>
                <Card className="divide-y divide-border overflow-hidden">
                  {g.items.map(r => <LogRow key={r.id} row={r} open={openIds.has(r.id)} onToggle={() => toggle(r.id)} onCompare={onCompare} />)}
                </Card>
              </section>
            ))}
            {rows.length >= limit && limit < MAX_LIMIT ? (
              <div className="flex justify-center"><Button onClick={() => setLimit(l => Math.min(l + PAGE, MAX_LIMIT))} loading={log.loading}>Load more</Button></div>
            ) : null}
          </div>
        )}
    </div>
  )
}

function LogRow({ row, open, onToggle, onCompare }: { row: ChangeLogEntry; open: boolean; onToggle: () => void; onCompare: (from: string, to: string) => void }) {
  const typeTone = row.change_type === 'added' ? 'ok' : row.change_type === 'removed' ? 'critical' : null
  const n = Object.keys(row.property_changes).length
  return (
    <div className={cn(open && 'bg-surface-2/50')}>
      <button onClick={onToggle} aria-expanded={open}
        className="w-full text-left px-4 py-2.5 grid grid-cols-[76px_auto_minmax(0,1fr)_16px] items-center gap-3 hover:bg-surface-2 transition-colors">
        <span className="text-[13px] text-muted tnum" title={formatDateTime(row.observed_at)}>{formatTime(row.observed_at)}</span>
        <span className="flex items-center gap-1.5">
          <Badge tone={significanceTone[row.significance]} dot>{row.significance}</Badge>
          {typeTone ? <Badge tone={typeTone}>{row.change_type}</Badge> : null}
        </span>
        <span className="min-w-0 flex items-baseline gap-2 flex-wrap">
          <span className="inline-flex items-center gap-1.5 text-sm font-semibold tracking-tight"><ResourceIcon type={row.resource_type} size={13} className="text-muted" />{row.resource_name}</span>
          <span className="text-xs text-faint"><ResourceTypeLabel type={row.resource_type} /></span>
          <span className="text-sm text-muted truncate">{row.summary}</span>
        </span>
        {open ? <ChevronDown size={15} className="text-faint" /> : <ChevronRight size={15} className="text-faint" />}
      </button>
      {open ? (
        <div className="px-4 pb-4 pl-[108px] space-y-3">
          <PropertyChanges change={row} />
          <div className="flex items-center gap-3 text-xs text-faint">
            <span>{n} {n === 1 ? 'property' : 'properties'} changed, observed {relativeTime(row.observed_at)}</span>
            <button className="text-accent hover:underline ml-auto" onClick={() => onCompare(row.from_snapshot_id, row.to_snapshot_id)}>Compare these two snapshots</button>
          </div>
        </div>
      ) : null}
    </div>
  )
}

/* ---------- Compare: on-demand diff between two snapshots ---------- */

function Compare({ connectionId, minSig, setMinSig, refreshKey, initialFrom, initialTo }: { connectionId: string; minSig: Significance | null; setMinSig: (s: Significance) => void; refreshKey: number; initialFrom: string | null; initialTo: string | null }) {
  const nav = useNavigate()
  const snaps = useAsync(() => getSnapshots(connectionId), [connectionId, refreshKey])
  const sorted = useMemo(() => (snaps.data ?? []).slice().sort((a, b) => b.created_at.localeCompare(a.created_at)), [snaps.data])
  const [from, setFrom] = useState<string>(initialFrom ?? '')
  const [to, setTo] = useState<string>(initialTo ?? '')

  // Default: TO = newest, FROM = the one before it. Keep choices when still valid.
  useEffect(() => {
    if (sorted.length === 0) return
    const ids = new Set(sorted.map(s => s.id))
    if (!ids.has(to)) setTo(sorted[0].id)
    if (!ids.has(from)) setFrom(sorted[1]?.id ?? sorted[0].id)
  }, [sorted, from, to])

  // Always compare oldest to newest. If the user picked FROM newer than TO, swap for the query and say so.
  const pickedFrom = sorted.find(s => s.id === from)
  const pickedTo = sorted.find(s => s.id === to)
  const swapped = !!pickedFrom && !!pickedTo && pickedFrom.created_at > pickedTo.created_at
  const queryFrom = swapped ? to : from
  const queryTo = swapped ? from : to
  const changes = useAsync(() => getChanges(connectionId, queryFrom, queryTo, minSig as Significance), [connectionId, queryFrom, queryTo, minSig], !!queryFrom && !!queryTo && minSig !== null)

  const groups: Record<Significance, Change[]> = { high: [], medium: [], low: [] }
  for (const c of changes.data ?? []) groups[c.significance].push(c)
  const total = changes.data?.length ?? 0
  const fromSnap = swapped ? pickedTo : pickedFrom
  const toSnap = swapped ? pickedFrom : pickedTo

  return (
    <div>
      <Card className="px-5 py-4 mb-6">
        {snaps.loading && !snaps.data ? <Skeleton className="h-9 w-full" />
          : snaps.error ? <ErrorState title="Snapshots unavailable" error={snaps.error} onRetry={snaps.reload} />
          : sorted.length < 2 ? (
            <EmptyState icon={<Camera size={20} />} title={sorted.length === 0 ? 'No snapshots yet' : 'Only one snapshot so far'}
              body="Two snapshots are needed to compare. Run Scan Now or wait for the next scheduled scan."
              action={<Button onClick={() => nav('/snapshots')}>Go to Snapshots</Button>} />
          ) : (
            <div className="grid gap-4 md:grid-cols-[1fr_auto_1fr] items-start">
              <SnapPicker label="From" value={from} onChange={setFrom} snaps={sorted} />
              <div className="hidden md:flex items-center justify-center pt-8 text-faint"><ArrowDown size={18} className="-rotate-90" /></div>
              <SnapPicker label="To" value={to} onChange={setTo} snaps={sorted} />
            </div>
          )}
        {swapped ? (
          <p role="status" className="mt-3 flex items-center gap-1.5 text-xs text-warning"><AlertTriangle size={13} /> FROM was newer than TO; snapshots swapped so changes read oldest to newest.</p>
        ) : null}
      </Card>

      {sorted.length >= 2 ? (
        changes.error ? <Card><ErrorState title="Could not compute changes" error={changes.error} onRetry={changes.reload} /></Card>
        : changes.loading && !changes.data ? <div className="space-y-3">{[0, 1, 2].map(i => <Skeleton key={i} className="h-28" />)}</div>
        : (
          <>
            <div className="flex flex-wrap items-baseline gap-x-6 gap-y-2 mb-6">
              <span className="text-3xl font-semibold tracking-tight tnum">{total} <span className="text-lg text-muted font-medium">{total === 1 ? 'change' : 'changes'}</span></span>
              <Summary n={groups.high.length} label="high impact" dot="bg-critical" />
              <Summary n={groups.medium.length} label="medium" dot="bg-warning" />
              <Summary n={groups.low.length} label="informational" dot="bg-faint" />
              {fromSnap && toSnap ? <span className="text-sm text-muted ml-auto">{formatDateTime(fromSnap.created_at)} to {formatDateTime(toSnap.created_at)}</span> : null}
            </div>
            {total === 0 ? (
              <Card><EmptyState icon={<GitCompareArrows size={20} />} title={minSig !== 'low' ? 'No changes at this significance' : 'No differences'}
                body={from === to ? 'FROM and TO are the same snapshot.' : minSig !== 'low' ? 'Lower significance changes may exist. Switch the filter to All to see everything.' : 'These two snapshots describe the same environment state.'}
                action={minSig !== 'low' ? <Button onClick={() => setMinSig('low')}>Show all</Button> : undefined} /></Card>
            ) : (
              <div className="space-y-8">
                {(['high', 'medium', 'low'] as Significance[]).map(sig => groups[sig].length === 0 ? null : (
                  <section key={sig}>
                    <h2 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-faint mb-3">
                      <span className={cn('h-2 w-2 rounded-full', sig === 'high' ? 'bg-critical' : sig === 'medium' ? 'bg-warning' : 'bg-faint')} />
                      {significanceLabel[sig]} <span className="tnum">{groups[sig].length}</span>
                    </h2>
                    <div className="grid gap-3 lg:grid-cols-2">
                      {groups[sig].map((c, i) => <ChangeRow key={`${c.resource_id}-${i}`} change={c} />)}
                    </div>
                  </section>
                ))}
              </div>
            )}
          </>
        )
      ) : null}
    </div>
  )
}

const FILTER_THRESHOLD = 30

// Snapshot picker grouped by day (optgroups), with a text filter once the list gets long.
// The selected snapshot always stays in the list so the control never points at a hidden option.
function SnapPicker({ label, value, onChange, snaps }: { label: string; value: string; onChange: (v: string) => void; snaps: SnapshotSummary[] }) {
  const [q, setQ] = useState('')
  const s = snaps.find(x => x.id === value)
  const needle = q.trim().toLowerCase()
  const visible = useMemo(() => !needle ? snaps : snaps.filter(x => x.id === value ||
    [x.label, tierLabel[snapshotTier(x)], formatDateTime(x.created_at), relativeTime(x.created_at)].some(t => t.toLowerCase().includes(needle))), [snaps, needle, value])
  const groups = useMemo(() => groupByDay(visible, x => x.created_at), [visible])
  return (
    <div className="block min-w-0">
      <span className="block text-[11px] font-semibold uppercase tracking-wider text-faint mb-1.5">{label}</span>
      {snaps.length > FILTER_THRESHOLD ? (
        <Input value={q} onChange={e => setQ(e.target.value)} placeholder={`Filter ${snaps.length} snapshots by label, tier or date`} className="mb-2 h-8 text-[13px]" aria-label={`Filter ${label} snapshots`} />
      ) : null}
      <Select value={value} onChange={e => onChange(e.target.value)} className="w-full h-10" aria-label={label}>
        {groups.map(g => (
          <optgroup key={g.key} label={g.label}>
            {g.items.map(x => <option key={x.id} value={x.id}>{formatTime(x.created_at)}  {x.label}  ({tierLabel[snapshotTier(x)]})</option>)}
          </optgroup>
        ))}
      </Select>
      {s ? <span className="block text-xs text-muted mt-1.5">{formatDateTime(s.created_at)}, {relativeTime(s.created_at)}, {tierLabel[snapshotTier(s)].toLowerCase()}{needle && visible.length !== snaps.length ? <span className="text-faint"> ({visible.length} of {snaps.length} shown)</span> : null}</span> : null}
    </div>
  )
}

function Summary({ n, label, dot }: { n: number; label: string; dot: string }) {
  return <span className="inline-flex items-center gap-2 text-sm"><span className={cn('h-2 w-2 rounded-full', dot)} /><span className="font-semibold tnum">{n}</span><span className="text-muted">{label}</span></span>
}
